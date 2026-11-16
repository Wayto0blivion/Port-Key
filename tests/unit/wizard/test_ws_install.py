"""/ws/install over a fake socket with a scripted wizard: the receive loop, actions, prompts,
close, idle timeout and errors."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.websockets import WebSocketState

from portkey.api.schemas.ws import NeedsActionMsg, StartMsg, StepMsg
from portkey.api.ws_install import install_ws
from portkey.config import Limits
from portkey.models import ConnectionRecord, HopRef
from portkey.ssh.errors import ConnectionRefused

TOKEN = "test-token-not-a-secret"
SECRET = "TEST-ONLY-socket-password-91c2"
HOP = HopRef(0, 1, None, "web-prod", "192.168.10.42", 22, "target")
NOW = datetime(2026, 11, 16, tzinfo=UTC)
RECORD = ConnectionRecord(
    "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", "web-prod", "192.168.10.42", 22, "john", "key",
    None, None, None, ("prod",), "red", False, "", 0, NOW, NOW, None, 0,
)  # fmt: skip
START = {"type": "start", "host": "192.168.10.42", "username": "john", "name": "web-prod"}
Script = Callable[[StartMsg, Any, Any], Awaitable[tuple[ConnectionRecord, str]]]


def text(data: Any) -> dict[str, Any]:
    return {"type": "websocket.receive", "text": json.dumps(data)}


class FakeWs:
    def __init__(self, services: Any) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(services=services))
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None
        self.application_state = WebSocketState.CONNECTING

    def push(self, *messages: Any) -> None:
        for m in messages:
            self.inbox.put_nowait(
                m if "type" in m and m["type"].startswith("websocket.") else text(m)
            )

    async def accept(self) -> None:
        self.application_state = WebSocketState.CONNECTED

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send_json(self, data: Any) -> None:
        self.sent.append(json.loads(json.dumps(data)))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = code
        self.application_state = WebSocketState.DISCONNECTED

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class FakeWizard:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.cancelled = False
        self.started: StartMsg | None = None

    async def run(self, start: StartMsg, prompter: Any, io: Any) -> tuple[ConnectionRecord, str]:
        self.started = start
        try:
            return await self.script(start, prompter, io)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def make(script: Script, idle: float = 600.0) -> tuple[FakeWs, FakeWizard]:
    wizard = FakeWizard(script)
    services = SimpleNamespace(
        config=SimpleNamespace(limits=replace(Limits(), wizard_idle_timeout=idle)),
        tokens=SimpleNamespace(verify=lambda token: token == TOKEN),
        wizard=wizard,
    )
    ws = FakeWs(services)
    ws.push({"type": "auth", "token": TOKEN})
    return ws, wizard


async def until(cond: Callable[[], bool]) -> None:
    for _ in range(500):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def done_script(start: StartMsg, prompter: Any, io: Any) -> tuple[ConnectionRecord, str]:
    await io.step(StepMsg(step="validate", state="ok"))
    return RECORD, "installed"


async def asks(start: StartMsg, prompter: Any, io: Any) -> tuple[ConnectionRecord, str]:
    msg = NeedsActionMsg(
        code="key_still_refused", message="m", actions=["fix_permissions", "cancel"]
    )
    action = await io.needs_action(msg)
    assert action == "fix_permissions"
    return RECORD, "installed"


async def hangs(start: StartMsg, prompter: Any, io: Any) -> tuple[ConnectionRecord, str]:
    await asyncio.Event().wait()
    raise AssertionError


async def test_start_to_done() -> None:
    ws, wizard = make(done_script)
    ws.push(START)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[0] == {"type": "step", "step": "validate", "state": "ok"}
    assert ws.sent[1]["type"] == "done"
    assert ws.sent[1]["outcome"] == "installed"
    assert ws.sent[1]["connection"]["tags"] == ["prod"]
    assert ws.sent[1]["connection"]["key_fp"] is None
    assert ws.closed == 1000
    assert wizard.started is not None
    assert wizard.started.name == "web-prod"


async def test_action_reaches_needs_action() -> None:
    ws, _ = make(asks)
    ws.push(START)
    task = asyncio.create_task(install_ws(ws))  # type: ignore[arg-type]
    await until(lambda: "needs_action" in ws.types())
    ws.push({"type": "ping"}, {"type": "action", "action": "fix_permissions"})
    await task
    assert ws.types() == ["needs_action", "pong", "done"]
    assert ws.sent[0]["actions"] == ["fix_permissions", "cancel"]


async def test_an_action_not_offered_is_bad_message() -> None:
    ws, wizard = make(asks)
    ws.push(START)
    task = asyncio.create_task(install_ws(ws))  # type: ignore[arg-type]
    await until(lambda: "needs_action" in ws.types())
    ws.push({"type": "action", "action": "save_password_only"})
    await task
    assert ws.sent[-1]["code"] == "bad_message"
    assert ws.closed == 4400
    assert wizard.cancelled


async def test_cancel_action_at_needs_action() -> None:
    async def cancelled(start: StartMsg, prompter: Any, io: Any) -> Any:
        msg = NeedsActionMsg(code="non_posix", message="m", actions=["check_key", "cancel"])
        assert await io.needs_action(msg) == "cancel"
        from portkey.ssh.errors import PromptCancelled

        raise PromptCancelled()

    ws, _ = make(cancelled)
    ws.push(START)
    task = asyncio.create_task(install_ws(ws))  # type: ignore[arg-type]
    await until(lambda: "needs_action" in ws.types())
    ws.push({"type": "action", "action": "cancel"})
    await task
    assert ws.sent[-1] == {
        "type": "error",
        "code": "cancelled",
        "message": "Cancelled.",
        "close": 1000,
    }


async def test_stale_action_ignored_but_cancel_always_works() -> None:
    ws, wizard = make(hangs)
    ws.push(START, {"type": "action", "action": "check_key"})
    task = asyncio.create_task(install_ws(ws))  # type: ignore[arg-type]
    await until(lambda: ws.inbox.empty())
    await asyncio.sleep(0.05)
    assert not task.done()
    ws.push({"type": "action", "action": "cancel"})
    await task
    assert ws.sent[-1]["code"] == "cancelled"
    assert ws.closed == 1000
    assert wizard.cancelled


async def test_prompt_reply_reaches_the_prompter_and_is_never_sent_back() -> None:
    got: list[str] = []

    async def prompts(start: StartMsg, prompter: Any, io: Any) -> Any:
        got.append(await prompter.password(HOP, "john", False))
        return RECORD, "installed"

    ws, _ = make(prompts)
    ws.push(START)
    task = asyncio.create_task(install_ws(ws))  # type: ignore[arg-type]
    await until(lambda: "prompt" in ws.types())
    pid = ws.sent[0]["id"]
    ws.push({"type": "prompt_reply", "id": "p99", "value": "stale"})
    ws.push({"type": "prompt_reply", "id": pid, "value": SECRET})
    await task
    assert got == [SECRET]
    assert SECRET not in json.dumps(ws.sent)
    assert ws.types() == ["prompt", "done"]


async def test_close_cancels_the_run() -> None:
    ws, wizard = make(hangs)
    ws.push(START, {"type": "close"})
    await install_ws(ws)  # type: ignore[arg-type]
    assert wizard.cancelled
    assert ws.sent == []
    assert ws.closed == 1000


async def test_socket_gone_cancels_the_run() -> None:
    ws, wizard = make(hangs)
    ws.push(START, {"type": "websocket.disconnect", "code": 1001})
    await install_ws(ws)  # type: ignore[arg-type]
    assert wizard.cancelled
    assert ws.sent == []


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "websocket.receive", "bytes": b"\x00"},
        START,  # a second start
        {"type": "open", "connection_id": RECORD.id, "cols": 80, "rows": 24},
        {"type": "action", "action": "nonsense"},
    ],
)
async def test_bad_messages_during_the_run(bad: dict[str, Any]) -> None:
    ws, wizard = make(hangs)
    ws.push(START, bad)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[-1]["code"] == "bad_message"
    assert ws.closed == 4400
    assert wizard.cancelled


@pytest.mark.parametrize(
    "first",
    [
        {"type": "action", "action": "cancel"},
        {"type": "prompt_reply", "id": "p1", "value": SECRET},
        {"type": "websocket.receive", "bytes": b"x"},
        {"type": "start", "host": "h", "username": "u"},  # no name
    ],
)
async def test_anything_but_start_first_is_bad_message(first: dict[str, Any]) -> None:
    ws, wizard = make(done_script)
    ws.push({"type": "ping"}, first)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.types() == ["pong", "error"]
    assert ws.sent[-1]["code"] == "bad_message"
    assert SECRET not in json.dumps(ws.sent)
    assert wizard.started is None


async def test_close_before_start() -> None:
    ws, wizard = make(done_script)
    ws.push({"type": "close"})
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent == []
    assert ws.closed == 1000
    assert wizard.started is None


async def test_ssh_errors_are_reported_with_their_close_code() -> None:
    async def refused(start: StartMsg, prompter: Any, io: Any) -> Any:
        raise ConnectionRefused("192.168.10.42 refused the connection on port 22.", hop=HOP)

    ws, _ = make(refused)
    ws.push(START)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[-1]["code"] == "refused"
    assert ws.sent[-1]["hop"]["connection_id"] is None
    assert ws.closed == 4502


async def test_unexpected_errors_are_internal() -> None:
    async def broken(start: StartMsg, prompter: Any, io: Any) -> Any:
        raise RuntimeError(f"boom {SECRET}")

    ws, _ = make(broken)
    ws.push(START)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[-1]["code"] == "internal"
    assert SECRET not in json.dumps(ws.sent)
    assert ws.closed == 1011


async def test_idle_before_start() -> None:
    ws, _ = make(done_script, idle=0.05)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[-1]["code"] == "prompt_timeout"
    assert ws.closed == 4408


async def test_idle_while_waiting_for_an_action() -> None:
    ws, wizard = make(asks, idle=0.1)
    ws.push(START)
    await install_ws(ws)  # type: ignore[arg-type]
    assert wizard.started is not None
    assert ws.types() == ["needs_action", "error"]
    assert ws.sent[-1]["code"] == "prompt_timeout"
    assert ws.closed == 4408


async def test_bad_token_never_reaches_the_wizard() -> None:
    ws, wizard = make(done_script)
    ws.inbox = asyncio.Queue()
    ws.push({"type": "auth", "token": "wrong"}, START)
    await install_ws(ws)  # type: ignore[arg-type]
    assert ws.sent[-1]["code"] == "unauthorized"
    assert wizard.started is None


def test_through_the_real_app(app: Any, services: Any) -> None:
    """Host/Origin guard, the real token check and the router, with a scripted wizard."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Starlette's test client warns about httpx versions
        from starlette.testclient import TestClient

        client = TestClient(app, base_url="http://localhost:8822")
    services.wizard = FakeWizard(done_script)
    with client:
        token = services.auth.setup("TEST-ONLY app password").token
        headers = {"host": "localhost:8822", "origin": "http://localhost:8822"}
        with client.websocket_connect("/ws/install", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.send_json(START)
            assert ws.receive_json() == {"type": "step", "step": "validate", "state": "ok"}
            done = ws.receive_json()
            assert done["type"] == "done"
            assert done["connection"]["name"] == "web-prod"
