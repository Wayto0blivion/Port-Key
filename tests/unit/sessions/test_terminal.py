"""TerminalSession: replay, attach/detach/reattach races, takeover, batching, exit, input."""

from __future__ import annotations

import asyncio

import pytest

from portkey.sessions.terminal import TerminalSession
from portkey.ssh.errors import ProtocolError
from tests.unit.sessions.fakes import (
    STARTED,
    FakeChain,
    FakeClock,
    RecordingSink,
    make_session,
    settle,
)


async def drained(sink: RecordingSink, total: int) -> None:
    await sink.wait_for(lambda: len(sink.data) >= total)


# --- start ---------------------------------------------------------------------------------------


async def test_start_opens_an_xterm_pty_in_bytes_mode() -> None:
    session, chain = await make_session(cols=0, rows=5000)
    assert chain.conn.options == {
        "term_type": "xterm-256color",
        "term_size": (1, 1000),
        "encoding": None,
    }
    assert (session.cols, session.rows) == (1, 1000)


async def test_a_refused_shell_names_the_hop() -> None:
    chain = FakeChain()
    chain.conn.fail = OSError("channel open failed")
    session = TerminalSession("s_x", "c", chain, 1024, STARTED)
    with pytest.raises(ProtocolError) as failed:
        await session.start(80, 24)
    assert failed.value.hop == chain.hops[0]
    assert "would not start a shell" in failed.value.message


# --- attach, replay and batching --------------------------------------------------------------


async def test_first_attach_replays_from_zero_then_streams() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    chain.channel.feed(b"motd\r\n")
    sink = RecordingSink()
    assert await session.attach(sink, None) == (0, True)
    chain.channel.feed(b"$ ")
    await drained(sink, 8)
    assert sink.first("ready") == (0, True)
    assert bytes(sink.data) == b"motd\r\n$ "
    assert sink.kinds()[0] == "ready"


async def test_small_writes_are_batched_into_frames() -> None:
    session, chain = await make_session(output_batch_seconds=0.05)
    sink = RecordingSink()
    await session.attach(sink, None)
    await settle()
    for i in range(50):
        chain.channel.feed(b"%02d" % i)
    await drained(sink, 100)
    frames = [n for kind, n in sink.events if kind == "output"]
    assert sum(frames) == 100
    assert len(frames) <= 3


async def test_frames_never_exceed_the_batch_size() -> None:
    session, chain = await make_session(output_batch_bytes=1000, output_batch_seconds=0.001)
    sink = RecordingSink()
    await session.attach(sink, None)
    chain.channel.feed(b"x" * 4500)
    await drained(sink, 4500)
    frames = [n for kind, n in sink.events if kind == "output"]
    assert frames == [1000, 1000, 1000, 1000, 500]


async def test_reattach_with_since_resumes_exactly_after_output_while_detached() -> None:
    clock = FakeClock()
    session, chain = await make_session(clock=clock, output_batch_seconds=0.001)
    first = RecordingSink("first")
    await session.attach(first, None)
    chain.channel.feed(b"one\r\n")
    await drained(first, 5)
    session.detach(first)
    assert session.detached_since == clock.now
    chain.channel.feed(b"two\r\n")  # produced while nobody is attached
    chain.channel.feed(b"three\r\n")
    second = RecordingSink("second")
    assert await session.attach(second, len(first.data)) == (5, False)
    chain.channel.feed(b"four\r\n")
    await drained(second, 18)
    assert bytes(first.data) + bytes(second.data) == b"one\r\ntwo\r\nthree\r\nfour\r\n"
    assert session.detached_since is None


async def test_output_arriving_during_the_attach_is_neither_lost_nor_doubled() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    stream = bytearray()
    for i in range(20):
        sink = RecordingSink(f"sink{i}")
        since = len(stream)
        offset, reset = await session.attach(sink, since)
        assert (offset, reset) == (since, False)
        # output lands after the cursor was taken and before ready was even sent
        piece = b"<%d>" % i
        chain.channel.feed(piece)
        await drained(sink, len(piece))
        stream += sink.data
    assert bytes(stream) == b"".join(b"<%d>" % i for i in range(20))


async def test_since_null_after_wrapping_resets_at_a_clean_line() -> None:
    session, chain = await make_session(buffer_bytes=60, output_batch_seconds=0.001)
    for i in range(10):
        chain.channel.feed(b"line %02d\r\n" % i)  # 9 bytes each, 90 in all
    sink = RecordingSink()
    offset, reset = await session.attach(sink, None)
    assert reset is True
    assert session.buffer.start == 30  # mid-line: "e 03\r\nline 04..."
    assert offset == 36  # skipped to after the next line feed
    await drained(sink, 90 - offset)
    assert bytes(sink.data).startswith(b"line 04\r\n")
    assert sink.first("ready") == (offset, True)


async def test_since_older_than_the_buffer_resets() -> None:
    session, chain = await make_session(buffer_bytes=15, output_batch_seconds=0.001)
    chain.channel.feed(b"aaaa\nbbbb\ncccc\ndddd\n")  # 20 bytes, start = 5 (the first "b")
    offset, reset = await session.attach(RecordingSink(), 2)
    assert reset is True
    assert offset == 10  # after the line feed that ends the cut "bbbb" line


async def test_since_beyond_what_was_sent_counts_as_everything() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    chain.channel.feed(b"abc")
    sink = RecordingSink()
    assert await session.attach(sink, 999) == (3, False)
    chain.channel.feed(b"d")
    await drained(sink, 1)
    assert bytes(sink.data) == b"d"


# --- takeover ----------------------------------------------------------------------------------


async def test_takeover_sends_detached_to_the_old_sink_after_its_last_frame() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    old = RecordingSink("old")
    await session.attach(old, None)
    chain.channel.feed(b"hello")
    await drained(old, 5)
    old.gate.clear()
    chain.channel.feed(b" world")  # the old sink is stuck sending this
    await asyncio.sleep(0.02)
    assert old.data == b"hello"
    new = RecordingSink("new")
    assert await session.attach(new, 5) == (5, False)
    old.gate.set()
    chain.channel.feed(b"!")
    await drained(new, 7)
    await old.wait_for(lambda: "detached" in old.kinds())
    assert old.kinds()[-1] == "detached"
    assert bytes(new.data) == b" world!"
    assert session.has_sink(new)
    assert not session.has_sink(old)
    session.detach(old)  # the old socket's handler detaching later changes nothing
    assert session.attached


# --- backpressure ------------------------------------------------------------------------------


async def test_a_slow_sink_pauses_reading_and_loses_nothing() -> None:
    session, chain = await make_session(
        buffer_bytes=4096, output_batch_bytes=512, output_batch_seconds=0.001
    )
    sink = RecordingSink()
    await session.attach(sink, None)
    await settle()
    sink.gate.clear()  # the browser stops reading
    sent = bytearray()
    for i in range(200):
        piece = bytes([65 + i % 26]) * 100
        sent += piece
        chain.channel.feed(piece)
    assert session.reading_paused
    assert chain.channel.paused
    assert session.unsent <= session.buffer_bytes  # nothing fell out of the buffer
    assert chain.channel.held  # the rest waits in the channel (SSH window)
    sink.gate.set()
    await drained(sink, len(sent))
    assert bytes(sink.data) == bytes(sent)
    assert not session.reading_paused
    assert "lagged" not in sink.kinds()


async def test_a_slow_sink_does_not_stall_another_session() -> None:
    slow_session, slow_chain = await make_session(buffer_bytes=2048, output_batch_seconds=0.001)
    fast_session, fast_chain = await make_session(output_batch_seconds=0.001)
    slow, fast = RecordingSink("slow"), RecordingSink("fast")
    await slow_session.attach(slow, None)
    await fast_session.attach(fast, None)
    await settle()
    slow.gate.clear()
    for _ in range(100):
        slow_chain.channel.feed(b"y\n" * 50)
    fast_chain.channel.feed(b"echo hi\r\nhi\r\n")
    await drained(fast, 13)
    assert slow_session.reading_paused
    assert not fast_session.reading_paused


async def test_a_detached_session_keeps_reading_and_keeps_the_newest_bytes() -> None:
    session, chain = await make_session(buffer_bytes=1024)
    for i in range(100):
        chain.channel.feed(b"%04d" % i * 25)  # 100 bytes each
    assert not chain.channel.paused
    assert session.buffer.total == 10_000
    assert session.buffer.read_from(None)[1].endswith(b"0099" * 25)


async def test_detaching_a_paused_session_resumes_reading() -> None:
    session, chain = await make_session(buffer_bytes=1024, output_batch_seconds=0.001)
    sink = RecordingSink()
    await session.attach(sink, None)
    await settle()
    sink.gate.clear()
    for _ in range(30):
        chain.channel.feed(b"z" * 100)
    assert chain.channel.paused
    session.detach(sink)
    assert not chain.channel.paused
    assert not chain.channel.held
    assert session.buffer.total == 3000


async def test_a_sink_that_falls_out_of_the_buffer_is_told_to_reconnect() -> None:
    # Only possible when one chunk is bigger than the headroom (not with SSH packets).
    session, chain = await make_session(buffer_bytes=8, output_batch_seconds=0.001)
    sink = RecordingSink()
    await session.attach(sink, None)
    await settle()
    chain.channel.feed(b"0123456789abcdef")
    await sink.wait_for(lambda: "lagged" in sink.kinds())
    assert not session.attached


# --- exit ----------------------------------------------------------------------------------------


async def test_remote_exit_flushes_output_then_reports_the_status() -> None:
    delivered: list[str] = []
    session, chain = await make_session(output_batch_seconds=0.001)
    session._on_delivered = lambda s: delivered.append(s.session_id)
    sink = RecordingSink()
    await session.attach(sink, None)
    chain.channel.feed(b"logout\r\n")
    chain.channel.finish(status=3)
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert bytes(sink.data) == b"logout\r\n"
    assert sink.first("exit") == (3, None, "closed")
    assert delivered == ["s_test"]
    await settle()
    assert chain.close_calls == 1  # the jump hops go too


async def test_a_signal_and_a_lost_connection() -> None:
    session, chain = await make_session()
    chain.channel.finish(signal="TERM")
    assert session.exit is not None
    assert (session.exit.exit_status, session.exit.signal, session.exit.reason) == (
        None,
        "TERM",
        "closed",
    )
    lost, lost_chain = await make_session()
    lost_chain.channel.finish(exc=ConnectionResetError("keepalive"))
    assert lost.exit is not None
    assert lost.exit.reason == "lost"


async def test_kill_ends_at_once_without_flushing() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    sink = RecordingSink()
    await session.attach(sink, None)
    await settle()
    sink.gate.clear()
    chain.channel.feed(b"x" * 1000)
    await session.close("killed")
    sink.gate.set()
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert sink.first("exit") == (None, None, "killed")
    assert sink.data == b""
    assert chain.close_calls == 1
    assert chain.channel.closed
    chain.channel.feed(b"late")  # nothing after a kill reaches the buffer
    assert session.buffer.total == 1000


async def test_an_exit_while_detached_waits_for_a_reattach() -> None:
    session, chain = await make_session(output_batch_seconds=0.001)
    chain.channel.feed(b"bye\r\n")
    chain.channel.finish(status=0)
    assert session.ended
    sink = RecordingSink()
    await session.attach(sink, 0)
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert sink.kinds() == ["ready", "output", "exit"]
    assert bytes(sink.data) == b"bye\r\n"


async def test_a_sink_that_goes_away_detaches() -> None:
    clock = FakeClock()
    session, chain = await make_session(clock=clock, output_batch_seconds=0.001)
    sink = RecordingSink()
    await session.attach(sink, None)
    sink.gone = True
    clock.advance(5)
    chain.channel.feed(b"x")
    for _ in range(50):
        if not session.attached:
            break
        await asyncio.sleep(0.005)
    assert not session.attached
    assert session.detached_since == clock.now
    assert session.summary().detached_at is not None


# --- input and resize ----------------------------------------------------------------------------


async def test_input_goes_to_the_channel_and_stops_after_exit() -> None:
    session, chain = await make_session()
    await session.write(b"ls\r")
    await session.write(b"")
    assert chain.channel.writes == [b"ls\r"]
    chain.channel.finish(status=0)
    await session.write(b"late")
    assert chain.channel.writes == [b"ls\r"]


async def test_input_after_the_remote_closed_is_dropped_until_the_exit_arrives() -> None:
    # Review finding: with reading paused (a slow browser), asyncssh refuses writes as soon as
    # the remote closes, but connection_lost waits for the held output. A keystroke in that
    # window raised BrokenPipeError, which the socket turned into "internal" (1011).
    session, chain = await make_session()
    sink = RecordingSink()
    sink.gate.clear()  # the browser is not reading
    await session.attach(sink, None)
    chain.channel.feed(b"x" * (session.high_water + 1))
    await settle()
    assert chain.channel.paused
    chain.channel.feed(b"tail")
    chain.channel.finish(status=0)
    assert session.exit is None  # the close waits for the held output
    await session.write(b"\x03")  # Ctrl+C while the output scrolls: dropped, no error
    assert chain.channel.writes == []
    sink.gate.set()
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert sink.first("exit") == (0, None, "closed")
    assert bytes(sink.data).endswith(b"tail")


async def test_input_waits_while_the_remote_is_not_taking_it() -> None:
    session, chain = await make_session()
    session.on_pause_writing()
    task = asyncio.create_task(session.write(b"big paste"))
    await settle()
    assert not task.done()
    session.on_resume_writing()
    await asyncio.wait_for(task, 1)
    assert chain.channel.writes == [b"big paste"]


async def test_resize_is_clamped() -> None:
    session, chain = await make_session()
    await session.resize(0, 5000)
    await session.resize(132, 43)
    assert chain.channel.sizes == [(80, 24), (1, 1000), (132, 43)]
