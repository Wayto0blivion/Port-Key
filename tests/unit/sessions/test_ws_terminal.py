"""/ws/terminal over a real uvicorn socket: open, prompts, input, resize, ping, close, errors."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from portkey.api.schemas.ws import OpenMsg
from portkey.api.ws_terminal import _TerminalSocket
from portkey.config import Limits
from portkey.ssh.errors import ConnectionRefused
from tests.unit.sessions.conftest import Live
from tests.unit.sessions.live import TermClient

UUID = "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a"


async def open_ready(client: TermClient, live: Live, **extra: Any) -> dict[str, Any]:
    await client.send(live.open_msg(**extra))
    return await client.until("ready")


async def test_open_relays_prompts_then_ready_then_output(live: Live) -> None:
    live.connector.ask_hostkey = True
    async with live.client() as client:
        await client.send(live.open_msg())
        status = await client.json()
        assert (status["type"], status["phase"]) == ("status", "connecting")
        assert status["hop"]["role"] == "target"
        prompt = await client.json()
        assert (prompt["type"], prompt["kind"]) == ("prompt", "hostkey_new")
        await client.send({"type": "prompt_reply", "id": prompt["id"], "accept": True})
        assert (await client.json())["phase"] == "ready"
        ready = await client.json()
        assert ready["type"] == "ready"
        assert ready["session_id"].startswith("s_")
        assert (ready["offset"], ready["reset"], ready["reattached"]) == (0, True, False)
        assert ready["connection"]["name"] == "web-prod"
        assert "last_used_at" in ready["connection"]  # required nullable fields stay
        channel = live.channel()
        channel.echo = True
        await client.send_bytes(b"echo hi\r")
        assert (await client.read_until(b"echo hi\r")).endswith(b"echo hi\r")
        assert channel.writes == [b"echo hi\r"]
        assert live.connector.chains[0].conn.options["term_size"] == (80, 24)


async def test_the_second_message_must_be_open_or_attach(live: Live) -> None:
    for first in ({"type": "ping"}, {"type": "resize", "cols": 1, "rows": 1}):
        async with live.client() as client:
            await client.send(first)
            error = await client.json()
            assert (error["code"], error["close"]) == ("bad_message", 4400)
            assert await client.close_code() == 4400
    async with live.client() as client:
        await client.ws.send("{not json")
        assert (await client.json())["code"] == "bad_message"


async def test_without_auth_nothing_opens(live: Live) -> None:
    async with TermClient.connect(live.port) as client:
        await client.send(live.open_msg())
        error = await client.json()
        assert (error["code"], error["close"]) == ("unauthorized", 4401)
    assert live.connector.chains == []


async def test_unknown_connection_is_4404(live: Live) -> None:
    async with live.client() as client:
        await client.send(live.open_msg(connection_id="9e8d7c6b-0000-4000-8000-000000000000"))
        error = await client.until("error")
        assert (error["code"], error["close"]) == ("not_found", 4404)
        assert await client.close_code() == 4404


async def test_an_ssh_failure_is_reported_with_its_code(live: Live) -> None:
    live.connector.fail = ConnectionRefused("web.example.test refused the connection.")
    async with live.client() as client:
        await client.send(live.open_msg())
        error = await client.until("error")
        assert (error["code"], error["close"]) == ("refused", 4502)
        assert error["message"] == "web.example.test refused the connection."
    assert live.services.sessions.list_all() == []


async def test_input_before_ready_is_dropped(live: Live) -> None:
    async with live.client() as client:
        await client.send_bytes(b"too early")
        await open_ready(client, live)
        await client.send_bytes(b"x")
        await client.send({"type": "ping"})
        assert (await client.until("pong")) == {"type": "pong"}
        assert live.channel().writes == [b"x"]


async def test_resize_is_clamped_and_one_during_connect_is_kept(live: Live) -> None:
    live.connector.block = asyncio.Event()
    async with live.client() as client:
        await client.send(live.open_msg())
        await client.until("status")
        await client.send({"type": "resize", "cols": 132, "rows": 43})
        await client.send({"type": "ping"})
        await client.until("pong")  # answered while connecting
        live.connector.block.set()
        await client.until("ready")
        await client.send({"type": "resize", "cols": 0, "rows": 5000})
        await client.send({"type": "ping"})
        await client.until("pong")
        assert live.channel().sizes == [(80, 24), (132, 43), (1, 1000)]


async def test_close_kills_the_session(live: Live) -> None:
    async with live.client() as client:
        ready = await open_ready(client, live)
        await client.send({"type": "close"})
        exit_msg = await client.until("exit")
        assert exit_msg == {"type": "exit", "exit_status": None, "signal": None, "reason": "killed"}
        assert await client.close_code() == 1000
    assert live.connector.chains[0].close_calls == 1
    async with live.client() as again:
        await again.send({"type": "attach", "session_id": ready["session_id"], "since": 0})
        error = await again.json()
        assert (error["code"], error["close"]) == ("session_gone", 4410)


async def test_close_while_connecting_cancels(live: Live) -> None:
    live.connector.block = asyncio.Event()
    async with live.client() as client:
        await client.send(live.open_msg())
        await client.until("status")
        await client.send({"type": "close"})
        error = await client.json()
        assert (error["code"], error["close"]) == ("cancelled", 1000)
        assert await client.close_code() == 1000
    assert live.connector.cancelled == 1
    assert live.services.sessions.list_all() == []


async def test_cancelling_a_prompt_ends_the_attempt(live: Live) -> None:
    live.connector.ask_hostkey = True
    async with live.client() as client:
        await client.send(live.open_msg())
        prompt = await client.until("prompt")
        await client.send({"type": "prompt_reply", "id": prompt["id"], "cancel": True})
        error = await client.until("error")
        assert (error["code"], error["close"]) == ("cancelled", 1000)


async def test_dropping_the_socket_while_connecting_aborts(live: Live) -> None:
    live.connector.block = asyncio.Event()
    async with live.client() as client:
        await client.send(live.open_msg())
        await client.until("status")
    for _ in range(100):
        if live.connector.cancelled:
            break
        await asyncio.sleep(0.01)
    assert live.connector.cancelled == 1
    assert live.services.sessions.list_all() == []


async def test_remote_exit_reports_status_and_closes(live: Live) -> None:
    async with live.client() as client:
        await open_ready(client, live)
        channel = live.channel()
        channel.feed(b"logout\r\n")
        channel.finish(status=42)
        exit_msg = await client.until("exit")
        assert exit_msg == {"type": "exit", "exit_status": 42, "signal": None, "reason": "closed"}
        assert client.output == b"logout\r\n"
        assert await client.close_code() == 1000
    assert live.services.sessions.list_all() == []


async def test_an_input_frame_over_64_kb_closes_with_1009(live: Live) -> None:
    async with live.client() as client:
        ready = await open_ready(client, live)
        await client.send_bytes(b"a" * (64 * 1024))  # exactly the cap is fine
        await client.send({"type": "ping"})
        await client.until("pong")
        await client.send_bytes(b"a" * (64 * 1024 + 1))
        assert await client.close_code() == 1009
    assert live.channel().writes == [b"a" * (64 * 1024)]
    [summary] = live.services.sessions.list_all()
    assert summary.session_id == ready["session_id"]  # only detached
    for _ in range(100):
        if not live.services.sessions.list_all()[0].attached:
            break
        await asyncio.sleep(0.01)
    assert live.services.sessions.list_all()[0].attached is False


async def test_the_socket_keeps_reading_while_the_remote_takes_no_input(live: Live) -> None:
    # Review finding: the receive loop waited inside session.write, so a stalled remote program
    # kept it from seeing ping, close, a drop or server shutdown (uvicorn then never reached
    # the lifespan shutdown).
    async with live.client() as client:
        ready = await open_ready(client, live)
        session = live.services.sessions.get(ready["session_id"])
        session.on_pause_writing()  # asyncssh: the remote is not taking input
        await client.send_bytes(b"a" * 1000)
        await client.send_bytes(b"b" * 1000)
        await client.send({"type": "ping"})
        assert await client.until("pong", seconds=2) == {"type": "pong"}
        assert live.channel().writes == [b"a" * 1000]  # the second waits its turn
        await client.send({"type": "close"})
        exit_msg = await client.until("exit", seconds=2)
        assert exit_msg["reason"] == "killed"
    assert live.services.sessions.list_all() == []


async def test_input_order_is_kept_once_the_remote_takes_input_again(live: Live) -> None:
    async with live.client() as client:
        ready = await open_ready(client, live)
        session = live.services.sessions.get(ready["session_id"])
        session.on_pause_writing()
        for chunk in (b"one ", b"two ", b"three"):
            await client.send_bytes(chunk)
        await client.send({"type": "ping"})
        await client.until("pong")
        session.on_resume_writing()
        for _ in range(100):
            if len(live.channel().writes) == 3:
                break
            await asyncio.sleep(0.01)
        assert live.channel().writes == [b"one ", b"two ", b"three"]


async def test_revoking_the_token_ends_the_open_socket(live_with: Any) -> None:
    # Security review: the token was checked once, in `auth`, so a thief's open shell kept
    # working after the owner logged out or changed the password.
    live: Live = await live_with(output_batch_seconds=0.002, ws_token_recheck=0.05)
    async with live.client() as client:
        ready = await open_ready(client, live)
        await asyncio.to_thread(live.services.tokens.revoke, live.token)
        error = await client.until("error", seconds=3)
        assert (error["code"], error["close"]) == ("unauthorized", 4401)
        assert await client.close_code() == 4401
    for _ in range(100):
        if not live.services.sessions.list_all()[0].attached:
            break
        await asyncio.sleep(0.01)
    [summary] = live.services.sessions.list_all()  # the shell stays, detached, for the owner
    assert (summary.session_id, summary.attached) == (ready["session_id"], False)


async def test_a_valid_token_keeps_the_socket_open(live_with: Any) -> None:
    live: Live = await live_with(output_batch_seconds=0.002, ws_token_recheck=0.02)
    async with live.client() as client:
        await open_ready(client, live)
        await asyncio.sleep(0.2)
        await client.send({"type": "ping"})
        assert await client.until("pong") == {"type": "pong"}


class _NoSocket:
    """A WebSocket whose client is already gone: every receive is a disconnect."""

    application_state = None

    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect", "code": 1006}


class _InstantRegistry:
    """``create`` finishes in its first step, as a connect can in the same loop turn as a drop."""

    def __init__(self) -> None:
        self.session = SimpleNamespace(session_id="s_race")
        self.killed: list[str] = []

    async def create(self, *args: Any) -> Any:
        return self.session

    async def kill(self, session_id: str) -> None:
        self.killed.append(session_id)


async def test_a_session_ready_in_the_same_turn_as_a_drop_is_killed() -> None:
    # Review finding: the drop branch dropped the finished session, which stayed open and
    # unattached (counting toward the 32) until the reaper or a reload found it.
    registry = _InstantRegistry()
    services = SimpleNamespace(config=SimpleNamespace(limits=Limits()), sessions=registry)
    sock = _TerminalSocket(_NoSocket(), services)  # type: ignore[arg-type]
    msg = OpenMsg(type="open", connection_id=UUID, cols=80, rows=24)
    assert await sock._open(msg) is False
    assert registry.killed == ["s_race"]


async def test_a_message_out_of_order_after_ready_detaches(live: Live) -> None:
    async with live.client() as client:
        ready = await open_ready(client, live)
        await client.send({"type": "prompt_reply", "id": "p9", "accept": True})  # stale: ignored
        await client.send(live.open_msg())
        error = await client.until("error")
        assert (error["code"], error["close"]) == ("bad_message", 4400)
    for _ in range(100):
        if not live.services.sessions.list_all()[0].attached:
            break
        await asyncio.sleep(0.01)
    [summary] = live.services.sessions.list_all()
    assert (summary.session_id, summary.attached) == (ready["session_id"], False)
