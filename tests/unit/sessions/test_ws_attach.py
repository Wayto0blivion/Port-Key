"""/ws/terminal over a real uvicorn socket: reattach, takeover, the cap, and backpressure."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from typing import Any

from tests.unit.sessions.conftest import Live
from tests.unit.sessions.live import TermClient


async def open_ready(client: TermClient, live: Live) -> dict[str, Any]:
    await client.send(live.open_msg())
    return await client.until("ready")


async def until(predicate: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


def attached(live: Live) -> bool:
    return any(s.attached for s in live.services.sessions.list_all())


async def test_reattach_with_since_gets_exactly_what_was_missed(live: Live) -> None:
    async with live.client() as first:
        ready = await open_ready(first, live)
        live.channel().feed(b"one\r\n")
        await first.read_until(b"one\r\n")
    await until(lambda: not attached(live))
    live.channel().feed(b"two\r\n")  # while nobody is attached
    async with live.client() as second:
        await second.send(
            {"type": "attach", "session_id": ready["session_id"], "since": first.received}
        )
        again = await second.until("ready")
        assert (again["offset"], again["reset"], again["reattached"]) == (5, False, True)
        assert again["session_id"] == ready["session_id"]
        live.channel().feed(b"three\r\n")
        await second.read_until(b"three\r\n")
        assert bytes(first.output + second.output) == b"one\r\ntwo\r\nthree\r\n"


async def test_reattach_after_a_reload_replays_everything(live: Live) -> None:
    async with live.client() as first:
        ready = await open_ready(first, live)
        live.channel().feed(b"$ ls\r\nfile\r\n")
        await first.read_until(b"file\r\n")
    live.channel().feed(b"$ ")
    async with live.client() as second:
        await second.send(
            {
                "type": "attach",
                "session_id": ready["session_id"],
                "since": None,
                "cols": 100,
                "rows": 30,
            }
        )
        again = await second.until("ready")
        assert (again["offset"], again["reset"]) == (0, True)
        await second.read_until(b"file\r\n$ ")
        assert bytes(second.output) == b"$ ls\r\nfile\r\n$ "
    assert live.channel().sizes[-1] == (100, 30)


async def test_attach_to_an_unknown_session_is_4410(live: Live) -> None:
    async with live.client() as client:
        await client.send({"type": "attach", "session_id": "s_gone", "since": 0})
        error = await client.json()
        assert error == {
            "type": "error",
            "code": "session_gone",
            "message": "That session has ended.",
            "close": 4410,
        }
        assert await client.close_code() == 4410


async def test_takeover_tells_the_old_socket_it_was_detached(live: Live) -> None:
    async with live.client() as first, live.client() as second:
        ready = await open_ready(first, live)
        live.channel().feed(b"hello")
        await first.read_until(b"hello")
        await second.send(
            {"type": "attach", "session_id": ready["session_id"], "since": first.received}
        )
        assert (await second.until("ready"))["reattached"] is True
        detached = await first.until("detached")
        assert detached == {"type": "detached", "reason": "attached_elsewhere"}
        assert await first.close_code() == 1000
        live.channel().echo = True
        await second.send_bytes(b"!")
        await second.read_until(b"!")
        assert bytes(first.output) == b"hello"
        assert live.channel().writes == [b"!"]


async def test_an_exit_while_detached_is_delivered_on_reattach(live: Live) -> None:
    async with live.client() as first:
        ready = await open_ready(first, live)
    await until(lambda: not attached(live))
    live.channel().feed(b"bye\r\n")
    live.channel().finish(status=0)
    assert live.services.sessions.list_all() == []  # ended: no longer listed
    async with live.client() as second:
        await second.send({"type": "attach", "session_id": ready["session_id"], "since": 0})
        await second.until("ready")
        exit_msg = await second.until("exit")
        assert (exit_msg["exit_status"], exit_msg["reason"]) == (0, "closed")
        assert bytes(second.output) == b"bye\r\n"
        assert await second.close_code() == 1000


async def test_too_many_sessions_is_4429(live_with: Callable[..., Any]) -> None:
    live: Live = await live_with(max_sessions=2, output_batch_seconds=0.002)
    async with live.client() as a, live.client() as b, live.client() as c:
        await open_ready(a, live)
        await open_ready(b, live)
        await c.send(live.open_msg())
        error = await c.json()
        assert (error["code"], error["close"]) == ("too_many_sessions", 4429)
        assert await c.close_code() == 4429
    assert len(live.connector.chains) == 2


async def test_a_slow_browser_pauses_its_session_only(live: Live) -> None:
    """The slow client stops reading: uvicorn's send waits, the ring buffer fills, the session
    stops reading SSH (the rest waits in the channel), memory stays bounded and another
    session keeps working. When the client reads again every byte arrives, in order."""
    async with live.client(max_queue=1) as slow, live.client() as other:
        await open_ready(slow, live)
        session = next(iter(live.services.sessions._sessions.values()))
        flood = live.channel(0)
        await open_ready(other, live)
        live.channel(1).echo = True
        sent = hashlib.sha256()
        total = 0
        chunk_no = 0
        while not session.reading_paused:
            chunk = (b"%07d " % chunk_no) * 4096  # 32 KB
            flood.feed(chunk)
            sent.update(chunk)
            total += len(chunk)
            chunk_no += 1
            assert total < 256 * 1024 * 1024, "the session never pushed back"
            await asyncio.sleep(0)
        for _ in range(64):  # 2 MB more: held by the channel, not by the session
            chunk = (b"%07d " % chunk_no) * 4096
            flood.feed(chunk)
            sent.update(chunk)
            total += len(chunk)
            chunk_no += 1
        assert session.reading_paused
        assert session.unsent <= session.buffer_bytes
        assert sum(len(c) for c in flood.held) >= 2 * 1024 * 1024
        started = time.monotonic()
        await other.send_bytes(b"still here")
        await other.read_until(b"still here", seconds=2)
        await other.send({"type": "ping"})
        await other.until("pong", seconds=2)
        assert time.monotonic() - started < 1.0
        while len(slow.output) < total:  # the browser reads again
            message = await asyncio.wait_for(slow.ws.recv(), 10)
            assert isinstance(message, bytes)
            slow.output += message
        assert len(slow.output) == total
        assert hashlib.sha256(slow.output).digest() == sent.digest()
        assert not session.reading_paused
