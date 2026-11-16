"""WsPrompter: prompt messages, ids, replies, cancel and timeout over a fake socket."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from portkey.api.schemas.ws import PromptReplyMsg
from portkey.api.ws_common import WsPrompter
from portkey.config import Limits
from portkey.models import KeyRecord
from portkey.ssh.errors import BadMessage, PromptCancelled, PromptTimeout
from tests.unit.ssh.fakes import FakeWs, hop

HOP = hop(name="web-prod", host="192.168.10.42")
HOP_JSON = {
    "index": 0,
    "count": 1,
    "connection_id": HOP.connection_id,
    "name": "web-prod",
    "host": "192.168.10.42",
    "port": 22,
    "role": "target",
}
SECRET = "hunter2-not-real"
KEY = KeyRecord(
    fp="SHA256:Xk3fP0aB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6r9Qa",
    name="id_ed25519",
    type="ed25519",
    bits=256,
    comment="",
    path=Path("/ssh/id_ed25519"),
    display_path="C:\\Users\\john\\.ssh\\id_ed25519",
    source="ssh_dir",
    encrypted=True,
    public_key="ssh-ed25519 AAAA",
)


async def prompter_on(prompt_timeout: float = 5.0) -> tuple[FakeWs, WsPrompter]:
    ws = FakeWs()
    await ws.accept()
    limits = replace(Limits(), prompt_timeout=prompt_timeout)
    return ws, WsPrompter(ws, limits)  # type: ignore[arg-type]


async def until_pending(p: WsPrompter, prompt_id: str | None = None) -> str:
    """Yield to the loop until a prompt (``prompt_id``, or any) waits for its reply."""
    while p.pending_id is None or (prompt_id is not None and p.pending_id != prompt_id):  # noqa: ASYNC110
        await asyncio.sleep(0)
    return p.pending_id


async def ask(p: WsPrompter, call: Awaitable[Any], **reply: Any) -> Any:
    """Run ``call``, answer its prompt with ``reply`` once it is up, return the result."""
    task = asyncio.ensure_future(call)
    waiting = asyncio.ensure_future(until_pending(p))
    await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        waiting.cancel()
        return task.result()
    p.deliver(PromptReplyMsg(type="prompt_reply", id=waiting.result(), **reply))
    return await task


async def test_hostkey_new_message_and_ids() -> None:
    ws, p = await prompter_on()
    assert await ask(p, p.hostkey_new(HOP, "ssh-ed25519", "SHA256:abc"), accept=True) is True
    assert ws.sent[0] == {
        "type": "prompt",
        "id": "p1",
        "kind": "hostkey_new",
        "hop": HOP_JSON,
        "host": "192.168.10.42",
        "port": 22,
        "key_type": "ssh-ed25519",
        "fingerprint": "SHA256:abc",
    }
    assert await ask(p, p.hostkey_new(HOP, "ssh-ed25519", "SHA256:abc"), accept=False) is False
    assert ws.sent[1]["id"] == "p2"
    assert p.pending_id is None


async def test_hostkey_changed_message() -> None:
    ws, p = await prompter_on()
    call = p.hostkey_changed(HOP, "ssh-ed25519", "SHA256:old", "SHA256:new", None)
    assert await ask(p, call, accept=True, confirm_name=" Web-Prod ") == (True, " Web-Prod ")
    msg = ws.sent[0]
    assert msg["first_seen_at"] is None  # nullable: present as null
    assert (msg["confirm_name"], msg["old_key_type"]) == ("web-prod", "ssh-ed25519")
    seen = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    call = p.hostkey_changed(
        HOP, "ssh-ed25519", "SHA256:old", "SHA256:new", seen, old_key_type="ssh-rsa"
    )
    assert await ask(p, call, accept=False, confirm_name="web-prod") == (False, "")
    assert ws.sent[1]["old_key_type"] == "ssh-rsa"
    assert ws.sent[1]["first_seen_at"] == "2026-03-02T10:00:00Z"


async def test_passphrase_prompt_and_reply() -> None:
    ws, p = await prompter_on()
    got = await ask(p, p.passphrase(HOP, KEY, True), value=SecretStr(SECRET), remember=True)
    assert got == (SECRET, True)
    assert ws.sent[0]["key_fp"] == KEY.fp
    assert ws.sent[0]["key_name"] == "id_ed25519"
    assert ws.sent[0]["path"] == "C:\\Users\\john\\.ssh\\id_ed25519"
    assert ws.sent[0]["retry"] is True
    assert await ask(p, p.passphrase(HOP, KEY, False), value=SecretStr(SECRET)) == (SECRET, False)


async def test_password_prompt() -> None:
    ws, p = await prompter_on()
    assert await ask(p, p.password(HOP, "john", False), value=SecretStr(SECRET)) == SECRET
    assert {k: ws.sent[0][k] for k in ("kind", "user", "host", "port", "retry")} == {
        "kind": "password",
        "user": "john",
        "host": "192.168.10.42",
        "port": 22,
        "retry": False,
    }


async def test_kbdint_prompt_and_answers() -> None:
    ws, p = await prompter_on()
    call = p.kbdint(HOP, "", "", [("Verification code: ", False)], retry=True)
    assert await ask(p, call, values=[SecretStr("492039")]) == ["492039"]
    assert ws.sent[0]["prompts"] == [{"text": "Verification code: ", "echo": False}]
    assert ws.sent[0]["retry"] is True
    assert await ask(p, p.kbdint(HOP, "", "Note", []), values=[]) == []
    assert await ask(p, p.kbdint(HOP, "", "Note", [])) == []


@pytest.mark.parametrize(
    ("call", "reply"),
    [
        ("hostkey_new", {}),
        ("password", {}),
        ("passphrase", {"remember": True}),
        ("kbdint", {"values": []}),
        ("kbdint", {}),
    ],
)
async def test_a_reply_missing_what_its_kind_needs_is_a_bad_message(
    call: str, reply: dict[str, Any]
) -> None:
    _, p = await prompter_on()
    calls = {
        "hostkey_new": lambda: p.hostkey_new(HOP, "ssh-ed25519", "SHA256:abc"),
        "password": lambda: p.password(HOP, "john", False),
        "passphrase": lambda: p.passphrase(HOP, KEY, False),
        "kbdint": lambda: p.kbdint(HOP, "", "", [("Code: ", False)]),
    }
    with pytest.raises(BadMessage):
        await ask(p, calls[call](), **reply)


async def test_cancel_reply() -> None:
    _, p = await prompter_on()
    with pytest.raises(PromptCancelled):
        await ask(p, p.password(HOP, "john", False), cancel=True)


async def test_stale_and_unknown_ids_are_ignored() -> None:
    _, p = await prompter_on()
    task = asyncio.ensure_future(p.password(HOP, "john", False))
    await until_pending(p)
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p9", value=SecretStr("wrong")))
    await asyncio.sleep(0.01)
    assert not task.done()
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p1", value=SecretStr(SECRET)))
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p1", value=SecretStr("again")))
    assert await task == SECRET


async def test_cancel_pending_fails_this_and_every_later_prompt() -> None:
    ws, p = await prompter_on()
    task = asyncio.ensure_future(p.hostkey_new(HOP, "ssh-ed25519", "SHA256:abc"))
    await until_pending(p)
    p.cancel_pending()
    with pytest.raises(PromptCancelled):
        await task
    with pytest.raises(PromptCancelled):
        await p.password(HOP, "john", False)
    assert len(ws.sent) == 1  # the later prompt was never sent


async def test_unanswered_prompt_times_out() -> None:
    _, p = await prompter_on(prompt_timeout=0.05)
    with pytest.raises(PromptTimeout) as timed_out:
        await p.password(HOP, "john", False)
    assert timed_out.value.close_code == 4408
    assert p.pending_id is None
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p1", value=SecretStr(SECRET)))  # ignored


async def test_one_prompt_at_a_time() -> None:
    ws, p = await prompter_on()
    first = asyncio.ensure_future(p.password(HOP, "john", False))
    second = asyncio.ensure_future(p.password(HOP, "john", True))
    await until_pending(p)
    await asyncio.sleep(0.01)
    assert len(ws.sent) == 1
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p1", value=SecretStr("one")))
    assert await first == "one"
    await until_pending(p, "p2")
    p.deliver(PromptReplyMsg(type="prompt_reply", id="p2", value=SecretStr("two")))
    assert await second == "two"


async def test_cancelled_waiter_clears_the_prompt() -> None:
    _, p = await prompter_on()
    task = asyncio.ensure_future(p.password(HOP, "john", False))
    await until_pending(p)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert p.pending_id is None


async def test_status_message() -> None:
    ws, p = await prompter_on()
    await p.status("auth", HOP, method="publickey", key_name="id_ed25519")
    await p.status("connecting", HOP)
    assert ws.sent == [
        {
            "type": "status",
            "phase": "auth",
            "hop": HOP_JSON,
            "method": "publickey",
            "key_name": "id_ed25519",
        },
        {"type": "status", "phase": "connecting", "hop": HOP_JSON},
    ]


async def test_replies_never_reach_a_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _, p = await prompter_on(prompt_timeout=0.05)
    await ask(p, p.password(HOP, "john", False), value=SecretStr(SECRET))
    await ask(p, p.passphrase(HOP, KEY, False), value=SecretStr(SECRET), remember=True)
    with pytest.raises(BadMessage) as bad:
        await ask(
            p, p.kbdint(HOP, "", "", [("a", False), ("b", False)]), values=[SecretStr(SECRET)]
        )
    assert SECRET not in str(bad.value)
    with pytest.raises(PromptTimeout):
        await p.password(HOP, "john", True)
    assert SECRET not in caplog.text
