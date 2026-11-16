"""One terminal session (package 5).

Owns the ``SshChain``, the PTY channel on the target (``term_type="xterm-256color"``,
``encoding=None``), the ``RingBuffer`` and at most one attached sink (a WebSocket). When the
WebSocket drops the session stays alive, detached, until the registry's reaper closes it after
``settings.detach_grace``; a new attach takes over and the old sink gets ``detached``. When SSH
ends the session ends with ``exit`` (reason ``closed`` or ``lost``); ``close`` ends it with
``killed``. Terminal input and output are never logged.

Output path and backpressure
    SSH output is written to the ring buffer as it arrives (``on_data``). Each attachment has
    its own pump task that sends the buffer from the attachment's cursor: first ``ready``, then
    binary frames of at most ``output_batch_bytes`` (64 KB), waiting ``output_batch_seconds``
    (16 ms) to gather more when less than a frame is waiting. Replay and live output are one
    stream from the ring buffer, so nothing is lost or sent twice across an attach, whatever
    arrives during it: attaching is synchronous (no await between choosing the start offset and
    registering the cursor).

    While a sink is attached and more than ``high_water`` (3/4 of the buffer) is unsent, the
    channel stops reading (``pause_reading``): asyncssh then holds at most one channel window
    (2 MiB) and stops granting window to the server, so a remote ``yes`` or ``cat`` blocks at
    the browser's speed, as in a real terminal. Reading resumes below ``low_water`` (1/4).
    Sending waits on the WebSocket's own flow control (uvicorn's ``send`` waits for the
    transport to drain), so a slow browser holds at most: the ring buffer, one channel window,
    one frame and the transport's buffer. A detached session never pauses: it keeps reading and
    the ring buffer keeps the newest 256 KB, as the protocol says. Each session has its own
    tasks, so one slow browser or one flood never stalls another session.

Ending
    An ended session that was attached delivers the rest of its output (not for ``killed``),
    then ``exit``, and reports ``on_delivered``. One that ended while detached keeps its buffer
    so a reattach still gets the output and the ``exit``; the registry drops it after the grace.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from portkey.config import Limits
from portkey.interfaces import SshChain
from portkey.models import ConnectionRecord, SessionSummary
from portkey.sessions._replay import SCAN_BYTES, safe_skip
from portkey.sessions._shell import ShellChannel, open_shell
from portkey.sessions.ringbuffer import RingBuffer

log = logging.getLogger("portkey.sessions")

ExitReason = Literal["closed", "lost", "killed"]
#: How long a socket that is being replaced or ended gets to take its last message.
SINK_TIMEOUT = 5.0


def utcnow() -> datetime:
    return datetime.now(UTC)


class SessionSink(Protocol):
    """Where an attached session sends things; implemented in ``api.ws_terminal``. Every method
    raises (any exception) once the socket is gone."""

    async def ready(self, offset: int, reset: bool) -> None: ...

    async def output(self, data: bytes) -> None: ...

    async def exited(
        self,
        exit_status: int | None,
        signal: str | None,
        reason: ExitReason,
    ) -> None: ...

    async def detached(self) -> None: ...

    async def lagged(self) -> None:
        """The sink fell out of the buffer (cannot happen while flow control works): close the
        socket so the client reattaches and gets a reset."""


@dataclass(frozen=True, slots=True)
class ExitInfo:
    exit_status: int | None
    signal: str | None
    reason: ExitReason


@dataclass(eq=False, slots=True)
class _Attachment:
    sink: SessionSink
    cursor: int  # absolute offset of the next byte to send
    reset: bool
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class TerminalSession:
    def __init__(
        self,
        session_id: str,
        connection_id: str,
        chain: SshChain,
        buffer_bytes: int,
        started_at: datetime,
        *,
        limits: Limits | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = utcnow,
        on_end: Callable[[TerminalSession], None] | None = None,
        on_delivered: Callable[[TerminalSession], None] | None = None,
    ) -> None:
        limits = limits or Limits()
        self.session_id = session_id
        self.connection_id = connection_id
        self.chain = chain
        self.buffer_bytes = buffer_bytes
        self.started_at = started_at
        self.buffer = RingBuffer(buffer_bytes)
        self.batch_bytes = limits.output_batch_bytes
        self.batch_seconds = limits.output_batch_seconds
        self.min_size = limits.terminal_min_size
        self.max_size = limits.terminal_max_size
        self.high_water = max(1, buffer_bytes * 3 // 4)
        self.low_water = buffer_bytes // 4
        #: The saved connection at open time, for ``ready`` if it has been deleted since.
        self.connection: ConnectionRecord | None = None
        self.cols = 0
        self.rows = 0
        self.exit: ExitInfo | None = None
        # Not attached yet: the reaper's clock runs until the first attach.
        self.detached_since: float | None = clock()
        self.detached_at: datetime | None = started_at
        self._clock = clock
        self._wall = wall
        self._on_end = on_end
        self._on_delivered = on_delivered
        self._chan: ShellChannel | None = None
        self._att: _Attachment | None = None
        self._paused = False
        self._writable = asyncio.Event()
        self._writable.set()
        self._status: int | None = None
        self._signal: str | None = None
        self._closing: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # --- state ---------------------------------------------------------------------------------

    @property
    def attached(self) -> bool:
        return self._att is not None

    @property
    def ended(self) -> bool:
        return self.exit is not None

    def has_sink(self, sink: SessionSink) -> bool:
        """True while ``sink`` is the attached one (not taken over, not detached)."""
        return self._att is not None and self._att.sink is sink

    @property
    def reading_paused(self) -> bool:
        return self._paused

    @property
    def unsent(self) -> int:
        """Bytes in the buffer the attached sink has not been sent (0 when detached)."""
        att = self._att
        return self.buffer.total - att.cursor if att is not None else 0

    def clamp(self, size: int) -> int:
        return max(self.min_size, min(self.max_size, int(size)))

    def summary(self) -> SessionSummary:
        return SessionSummary(
            session_id=self.session_id,
            connection_id=self.connection_id,
            started_at=self.started_at,
            attached=self.attached,
            detached_at=None if self.attached else self.detached_at,
        )

    # --- lifecycle -----------------------------------------------------------------------------

    async def start(self, cols: int, rows: int) -> None:
        """Open the PTY shell and start pumping output."""
        self.cols, self.rows = self.clamp(cols), self.clamp(rows)
        hop = self.chain.hops[-1]
        self._chan = await open_shell(self.chain.target, self, self.cols, self.rows, hop)

    async def close(self, reason: ExitReason = "killed") -> None:
        """End the session (an attached sink gets ``exit``) and close every hop."""
        self._end(ExitInfo(None, None, reason))
        if self._closing is not None:
            await asyncio.shield(self._closing)

    def cancel_tasks(self) -> None:
        """Stop every pump and helper task (app shutdown)."""
        for task in list(self._tasks):
            task.cancel()

    def _end(self, info: ExitInfo) -> None:
        if self.exit is not None:
            return
        self.exit = info
        self._writable.set()
        if self._att is not None:
            self._att.wake.set()
        self._closing = self._spawn(self._close_chain())
        log.info("session %s ended: %s", self.session_id, info.reason)
        if self._on_end is not None:
            self._on_end(self)

    async def _close_chain(self) -> None:
        if self._chan is not None:
            with contextlib.suppress(Exception):
                self._chan.close()
        try:
            await self.chain.close()
        except Exception as exc:
            log.warning("session %s: closing SSH failed (%s)", self.session_id, type(exc).__name__)

    # --- ShellEvents (asyncssh callbacks, on the event loop) -----------------------------------

    def on_data(self, data: bytes) -> None:
        if self.exit is not None and self.exit.reason == "killed":
            return
        self.buffer.write(data)
        att = self._att
        if att is not None:
            att.wake.set()
            if not self._paused and self.buffer.total - att.cursor >= self.high_water:
                self._pause()

    def on_exit_status(self, status: int) -> None:
        self._status = status

    def on_exit_signal(self, signal: str) -> None:
        self._signal = signal

    def on_closed(self, exc: Exception | None) -> None:
        self._end(ExitInfo(self._status, self._signal, "lost" if exc is not None else "closed"))

    def on_pause_writing(self) -> None:
        self._writable.clear()

    def on_resume_writing(self) -> None:
        self._writable.set()

    # --- input ---------------------------------------------------------------------------------

    async def write(self, data: bytes) -> None:
        """Send input to the shell. Waits while the remote is not taking input (a large paste
        into a stalled program) so input cannot pile up without bound. Input to a channel the
        remote has already closed is dropped: asyncssh stops sending at once on the remote's
        close, but ``on_closed`` (and so the ``exit``) can come seconds later while reading is
        paused, and a keystroke then must not turn the exit into an internal error."""
        if self.exit is not None or self._chan is None or not data:
            return
        try:
            self._chan.write(data)
        except OSError:  # BrokenPipeError: "Channel not open for sending"
            return
        if not self._writable.is_set():
            await self._writable.wait()

    async def resize(self, cols: int, rows: int) -> None:
        """Clamped to 1..1000."""
        self.cols, self.rows = self.clamp(cols), self.clamp(rows)
        if self.exit is None and self._chan is not None:
            with contextlib.suppress(Exception):
                self._chan.change_terminal_size(self.cols, self.rows)

    # --- attach and detach ---------------------------------------------------------------------

    async def attach(self, sink: SessionSink, since: int | None) -> tuple[int, bool]:
        """Attach ``sink`` (the previous one gets ``detached``). Returns ``(offset, reset)``; the
        sink's ``ready`` gets the same values, then the replay from ``offset`` and live output
        follow as one stream. No await happens before the cursor is registered."""
        offset, reset = self.replay_start(since)
        old = self._att
        att = _Attachment(sink, offset, reset)
        self._att = att
        self.detached_since = None
        self.detached_at = None
        if old is not None:
            if old.task is not None:
                old.task.cancel()
            self._spawn(self._send_detached(old))
        att.task = self._spawn(self._pump(att))
        self._flow()
        return offset, reset

    def replay_start(self, since: int | None) -> tuple[int, bool]:
        """``(offset, reset)`` for an attach with ``since`` (see docs/ws-protocol.md "Replay"
        and ``_replay`` for the trimmed start of a wrapped buffer)."""
        buf = self.buffer
        if since is not None and since >= buf.start:
            return min(since, buf.total), False
        offset = buf.start
        if offset > 0:
            _, head = buf.read_from(offset, SCAN_BYTES)
            offset += safe_skip(head)
        return offset, True

    def detach(self, sink: SessionSink) -> None:
        """Called when ``sink``'s WebSocket drops; starts the detach-grace timer."""
        att = self._att
        if att is None or att.sink is not sink:
            return
        self._att = None
        if att.task is not None and att.task is not asyncio.current_task():
            att.task.cancel()
        self.detached_since = self._clock()
        self.detached_at = self._wall()
        self._flow()

    async def _send_detached(self, old: _Attachment) -> None:
        if old.task is not None:
            await asyncio.wait({old.task})  # its last frame is out before "detached"
        with contextlib.suppress(Exception):
            await asyncio.wait_for(old.sink.detached(), SINK_TIMEOUT)

    # --- output --------------------------------------------------------------------------------

    async def _pump(self, att: _Attachment) -> None:
        sink = att.sink
        try:
            await sink.ready(att.cursor, att.reset)
            while await self._next_frame(att):
                offset, data = self.buffer.read_from(att.cursor, self.batch_bytes)
                if offset != att.cursor:
                    log.warning("session %s: a sink fell behind the buffer", self.session_id)
                    self.detach(sink)
                    await sink.lagged()
                    return
                await sink.output(data)
                att.cursor += len(data)
                self._flow()
            assert self.exit is not None
            await sink.exited(self.exit.exit_status, self.exit.signal, self.exit.reason)
        except asyncio.CancelledError:
            raise
        except Exception:  # the socket is gone; the handler sees the disconnect too
            self.detach(sink)
            return
        if self._att is att:
            self._att = None
            if self._on_delivered is not None:
                self._on_delivered(self)

    async def _next_frame(self, att: _Attachment) -> bool:
        """Wait until a frame should be sent (True) or the exit is due (False)."""
        while True:
            killed = self.exit is not None and self.exit.reason == "killed"
            if killed or (self.exit is not None and self.buffer.total <= att.cursor):
                return False
            pending = self.buffer.total - att.cursor
            if pending <= 0:
                att.wake.clear()
                await att.wake.wait()
                continue
            if pending < self.batch_bytes and self.exit is None:
                await asyncio.sleep(self.batch_seconds)  # gather about one frame's worth
            return True

    # --- flow control --------------------------------------------------------------------------

    def _flow(self) -> None:
        att = self._att
        unsent = self.buffer.total - att.cursor if att is not None else 0
        if att is not None and not self._paused and unsent >= self.high_water:
            self._pause()
        elif self._paused and (att is None or unsent <= self.low_water):
            self._resume()

    def _pause(self) -> None:
        if self._chan is None or self.exit is not None:
            return
        self._paused = True
        self._chan.pause_reading()

    def _resume(self) -> None:
        self._paused = False
        if self._chan is not None:
            self._chan.resume_reading()  # may deliver held data (and pause again) right here

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
