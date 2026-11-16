"""Terminal sessions against the sshd test bed through /ws/terminal."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from tests.fixtures import testbed as tb
from tests.fixtures.prompter import kbdint_answers
from tests.integration.sessions.conftest import AppLive
from tests.integration.ssh.helpers import established_to, wait_for_established
from tests.unit.sessions.live import TermClient

PROMPT = b"$ "


async def ready_shell(client: TermClient, msg: dict[str, Any]) -> dict[str, Any]:
    """Open, wait for ``ready`` and the first shell prompt."""
    await client.send(msg)
    ready = await client.until("ready", seconds=20)
    await client.read_until(PROMPT, seconds=10)
    return ready


async def run(client: TermClient, command: str, marker: bytes, seconds: float = 10) -> bytes:
    await client.send_bytes(command.encode() + b"\r")
    return await client.read_until(marker, seconds=seconds)


async def test_echo_round_trip(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client() as client:
        ready = await ready_shell(client, app_live.open_msg(conn))
        assert (ready["offset"], ready["reset"], ready["reattached"]) == (0, True, False)
        assert ready["connection"]["id"] == conn.id
        out = await run(client, "echo port-key-$((6*7))", b"port-key-42\r\n")
        assert b"port-key-42\r\n" in out
        out = await run(client, "printf '\\xe2\\x82\\xac\\n'", "€\r\n".encode())
        assert "€".encode() in out  # bytes mode: UTF-8 arrives untouched
        await run(client, "echo $TERM", b"xterm-256color\r\n")


async def test_resize_is_visible_to_the_remote(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client() as client:
        await ready_shell(client, app_live.open_msg(conn, cols=100, rows=30))
        await run(client, "stty size", b"30 100\r\n")
        await client.send({"type": "resize", "cols": 132, "rows": 43})
        await run(client, "stty size", b"43 132\r\n")


async def test_exit_code_and_reason(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client() as client:
        await ready_shell(client, app_live.open_msg(conn))
        await client.send_bytes(b"exit 3\r")
        exit_msg = await client.until("exit", seconds=10)
        assert exit_msg == {"type": "exit", "exit_status": 3, "signal": None, "reason": "closed"}
        assert await client.close_code() == 1000
    assert app_live.services.sessions.list_all() == []


async def test_reattach_with_since_after_output_while_detached(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client() as first:
        ready = await ready_shell(first, app_live.open_msg(conn))
        await run(first, "echo first-$((1+1))", b"first-2\r\n")
        await first.read_until(PROMPT)
        await first.send_bytes(b"sleep 1; echo later-$((40+2))\r")
        await first.read_until(b"later-$((40+2))\r\n")  # the echoed command line only
    session = app_live.session(ready["session_id"])
    await asyncio.sleep(2)  # "later-42" is printed while nobody is attached
    assert not session.attached
    assert b"later-42" in session.buffer.read_from(first.received)[1]
    async with app_live.client() as second:
        await second.send(
            {"type": "attach", "session_id": ready["session_id"], "since": first.received}
        )
        again = await second.until("ready")
        assert (again["offset"], again["reset"], again["reattached"]) == (
            first.received,
            False,
            True,
        )
        await second.read_until(b"later-42\r\n")
        stream = bytes(first.output + second.output)
    async with app_live.client() as third:  # a reload: the full replay is the same stream
        await third.send({"type": "attach", "session_id": ready["session_id"], "since": None})
        replay = await third.until("ready")
        assert (replay["offset"], replay["reset"]) == (0, True)
        assert (await third.read_bytes(len(stream))).startswith(stream)
    assert stream.count(b"later-42\r\n") == 1


async def test_a_flood_loses_nothing_and_the_server_stays_responsive(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client() as flood, app_live.client() as other:
        await ready_shell(flood, app_live.open_msg(conn))
        await ready_shell(other, app_live.open_msg(conn))
        await flood.send_bytes(b"seq 1 400000; echo flood-$((3*3))-done\r")
        await asyncio.sleep(0.3)  # the flood is running
        started = time.monotonic()
        await run(other, "echo alive-$((5*5))", b"alive-25\r\n")
        await flood.send({"type": "ping"})
        await flood.until("pong", seconds=5)
        assert time.monotonic() - started < 3.0
        out = await flood.read_until(b"flood-9-done\r\n", seconds=120)
    body = out.split(b"flood-$((3*3))-done\r\n", 1)[1]
    # bash ends bracketed paste ("\x1b[?2004l\r") just before the first line of output
    numbers = re.findall(rb"(?:^|\r)(\d+)\r$", body, flags=re.MULTILINE)
    assert [int(n) for n in numbers] == list(range(1, 400001))


async def test_a_slow_browser_pushes_back_on_the_remote(app_live: AppLive) -> None:
    """The browser stops reading while the remote writes 30 MB. Once the socket buffers are
    full the session stays paused, the remote is held back (it has not written everything),
    and the session holds no more than its ring buffer. Compression is off for this client:
    a ``y`` flood would otherwise deflate to almost nothing on the wire."""
    conn = await app_live.add("sshd-keyonly", tb.SSHD.host, "keyonly")
    async with app_live.client(max_queue=1, compression=None) as slow:
        ready = await ready_shell(slow, app_live.open_msg(conn))
        session = app_live.session(ready["session_id"])
        await slow.send_bytes(
            b"head -c 30000000 /dev/zero | tr '\\0' y; echo; echo slow-$((2+2))-done\r"
        )
        deadline = time.monotonic() + 30
        last = -1
        while not (session.reading_paused and session.buffer.total == last):
            assert time.monotonic() < deadline, "the session never stayed paused"
            last = session.buffer.total
            await asyncio.sleep(0.5)
        assert session.buffer.total < 30_000_000  # the remote is held back
        assert session.unsent <= session.buffer_bytes
        await slow.read_until(b"slow-4-done\r\n", seconds=120)
        assert bytes(slow.output).count(b"y") >= 30_000_000
        assert not session.reading_paused


async def test_through_the_jump_host(app_live: AppLive) -> None:
    before = established_to(22)
    jump = await app_live.add("jump", tb.SSHD.host, "keyonly")
    inner = await app_live.add("inner", tb.SSHD_INNER.host, "keyonly", jump=jump)
    async with app_live.client() as client:
        await client.send(app_live.open_msg(inner))
        phases = []
        while True:
            msg = await client.json(seconds=20)
            if msg["type"] != "status":
                break
            phases.append((msg["hop"]["index"], msg["phase"]))
        assert msg["type"] == "ready"
        assert phases == [
            (0, "connecting"),
            (0, "hostkey"),
            (0, "auth"),
            (1, "connecting"),
            (1, "hostkey"),
            (1, "auth"),
            (1, "ready"),
        ]
        await client.read_until(PROMPT, seconds=10)
        out = await run(client, "echo inner-$((7*6)) $(hostname)", b"inner-42 ")
        assert b"inner-42 " in out
        assert established_to(22) == before + 1  # the inner hop runs inside the jump's channel
        await client.send({"type": "close"})
        assert (await client.until("exit"))["reason"] == "killed"
        assert await client.close_code() == 1000
    assert await wait_for_established(22, before) == before  # every hop is closed


async def test_password_prompt_over_the_socket(app_live: AppLive) -> None:
    conn = await app_live.add("sshd-pw", tb.SSHD.host, "pw", auth_method="password")
    async with app_live.client() as client:
        await client.send(app_live.open_msg(conn))
        prompt = await client.until("prompt", seconds=20)
        assert (prompt["kind"], prompt["user"], prompt["retry"]) == ("password", "pw", False)
        await client.send(
            {"type": "prompt_reply", "id": prompt["id"], "value": tb.USERS["pw"].password}
        )
        await client.until("ready", seconds=20)
        await run(client, "whoami", b"pw\r\n")


async def test_first_seen_host_key_and_two_factor_over_the_socket(app_live: AppLive) -> None:
    conn = await app_live.add("rekeyed-tfa", tb.SSHD_REKEYED.host, "tfa", auth_method="password")
    answers = kbdint_answers(tb.USERS["tfa"].password, tb.Totp())
    async with app_live.client() as client:
        await client.send(app_live.open_msg(conn))
        prompt = await client.until("prompt", seconds=20)
        assert prompt["kind"] == "hostkey_new"
        assert prompt["fingerprint"] == tb.hostkey_fingerprint("b")
        await client.send({"type": "prompt_reply", "id": prompt["id"], "accept": True})
        while True:
            msg = await client.json(seconds=20)
            if msg["type"] == "ready":
                break
            if msg["type"] == "prompt":
                assert msg["kind"] == "kbdint"
                values = answers(prompts=[(p["text"], p["echo"]) for p in msg["prompts"]])
                await client.send({"type": "prompt_reply", "id": msg["id"], "values": values})
            else:
                assert msg["type"] == "status", msg
        await run(client, "whoami", b"tfa\r\n")
