"""Fakes for package 5's tests (no network).

- ``FakeChannel``: an SSH PTY channel driven by the test: ``feed(data)`` is output from the
  shell, ``finish(status)`` ends it. It honours ``pause_reading`` as asyncssh does: data fed
  while paused is held and delivered on ``resume_reading``, and a close waits for held data.
- ``FakeConn``: ``create_session`` builds a ``FakeChannel`` around the session's callbacks.
- ``FakeChain``: ``SshChain`` over a ``FakeConn``; counts ``close`` calls.
- ``FakeConnector``: ``SshConnector`` for the WebSocket tests: ``plan`` knows saved ids,
  ``connect`` can ask one host-key prompt, block until released, or fail.
- ``FakeClock``: a monotonic clock the test moves with ``advance``.
- ``RecordingSink``: a ``SessionSink`` that records everything; ``gate`` holds ``output``.
- ``make_session(...)``: a started ``TerminalSession`` on a fresh fake chain.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from portkey.config import Limits
from portkey.models import HopRef, HopTarget
from portkey.sessions.terminal import TerminalSession
from portkey.ssh.errors import SshError, UnknownConnection

TARGET = HopRef(
    0, 1, "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", "web-prod", "web.example.test", 22, "target"
)
STARTED = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


class FakeChannel:
    def __init__(self, session: Any, cols: int, rows: int) -> None:
        self.session = session  # the asyncssh SSHClientSession the code under test built
        self.writes: list[bytes] = []
        self.sizes: list[tuple[int, int]] = [(cols, rows)]
        self.paused = False
        self.pause_calls = 0
        self.held: list[bytes] = []
        self.closed = False
        self.echo = False
        self._close_pending: Exception | bool | None = False

    # --- what the session calls ---

    def write(self, data: bytes) -> None:
        if self._close_pending is True or self.closed:
            # asyncssh 2.24 stops sending as soon as the remote closes the channel, while
            # connection_lost waits for held data (reading paused).
            raise BrokenPipeError("Channel not open for sending")
        self.writes.append(data)
        if self.echo:
            self.feed(data)

    def change_terminal_size(self, width: int, height: int) -> None:
        self.sizes.append((width, height))

    def pause_reading(self) -> None:
        self.paused = True
        self.pause_calls += 1

    def resume_reading(self) -> None:
        self.paused = False
        while self.held and not self.paused:
            self.session.data_received(self.held.pop(0), None)
        self._maybe_close()

    def close(self) -> None:
        if not self.closed:
            self.finish()

    # --- what the test calls ---

    def feed(self, data: bytes) -> None:
        if self.paused:
            self.held.append(data)
        else:
            self.session.data_received(data, None)

    def finish(
        self, status: int | None = None, signal: str | None = None, exc: Exception | None = None
    ) -> None:
        """The shell exits (``status``/``signal``) or the connection is lost (``exc``)."""
        if status is not None:
            self.session.exit_status_received(status)
        if signal is not None:
            self.session.exit_signal_received(signal, False, "", "")
        if exc is not None:
            self.held.clear()
            self._close(exc)
            return
        self._close_pending = True
        self._maybe_close()

    def _maybe_close(self) -> None:
        if self._close_pending is True and not self.held and not self.closed:
            self._close(None)

    def _close(self, exc: Exception | None) -> None:
        self.closed = True
        self.session.connection_lost(exc)


class FakeConn:
    def __init__(self) -> None:
        self.channel: FakeChannel | None = None
        self.options: dict[str, Any] = {}
        self.fail: Exception | None = None

    async def create_session(self, factory: Callable[[], Any], **options: Any) -> Any:
        if self.fail is not None:
            raise self.fail
        self.options = options
        cols, rows = options["term_size"]
        self.channel = FakeChannel(factory(), cols, rows)
        return self.channel, self.channel.session


class FakeChain:
    def __init__(self, hops: list[HopRef] | None = None) -> None:
        self.conn = FakeConn()
        self._hops = hops or [TARGET]
        self.close_calls = 0

    @property
    def target(self) -> Any:
        return self.conn

    @property
    def hops(self) -> list[HopRef]:
        return list(self._hops)

    @property
    def channel(self) -> FakeChannel:
        assert self.conn.channel is not None
        return self.conn.channel

    async def close(self) -> None:
        self.close_calls += 1


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSink:
    """Records ``ready``/``output``/``exited``/``detached``. Clear ``gate`` to hold output."""

    def __init__(self, name: str = "sink") -> None:
        self.name = name
        self.events: list[tuple[str, Any]] = []
        self.data = bytearray()
        self.gate = asyncio.Event()
        self.gate.set()
        self.gone = False
        self.changed = asyncio.Event()

    def _note(self, kind: str, value: Any) -> None:
        self.events.append((kind, value))
        self.changed.set()

    async def ready(self, offset: int, reset: bool) -> None:
        self._note("ready", (offset, reset))

    async def output(self, data: bytes) -> None:
        await self.gate.wait()
        if self.gone:
            raise ConnectionResetError("socket gone")
        self.data += data
        self._note("output", len(data))

    async def exited(self, exit_status: int | None, signal: str | None, reason: str) -> None:
        self._note("exit", (exit_status, signal, reason))

    async def detached(self) -> None:
        self._note("detached", None)

    async def lagged(self) -> None:
        self._note("lagged", None)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]

    def first(self, kind: str) -> Any:
        return next(v for k, v in self.events if k == kind)

    async def wait_for(self, predicate: Callable[[], bool], seconds: float = 2.0) -> None:
        async def poll() -> None:
            while not predicate():
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(poll(), seconds)


async def make_session(
    *,
    buffer_bytes: int = 256 * 1024,
    clock: FakeClock | None = None,
    cols: int = 80,
    rows: int = 24,
    **limits: Any,
) -> tuple[TerminalSession, FakeChain]:
    chain = FakeChain()
    session = TerminalSession(
        "s_test",
        TARGET.connection_id or "",
        chain,
        buffer_bytes,
        STARTED,
        limits=Limits(ring_buffer_bytes=buffer_bytes, **limits),
        clock=clock or FakeClock(),
    )
    await session.start(cols, rows)
    return session, chain


async def settle(rounds: int = 5) -> None:
    """Let the session's tasks run (several loop iterations)."""
    for _ in range(rounds):
        await asyncio.sleep(0)


class FakeConnector:
    """``SshConnector`` for WebSocket tests. ``known`` maps connection ids to hop names."""

    def __init__(self) -> None:
        self.known: dict[str, str] = {}
        self.ask_hostkey = False
        self.block: asyncio.Event | None = None
        self.fail: SshError | None = None
        self.chains: list[FakeChain] = []
        self.cancelled = 0
        self.connecting = asyncio.Event()

    def plan(self, conn_id: str, force_password: bool = False) -> list[HopTarget]:
        if conn_id not in self.known:
            raise UnknownConnection()
        ref = HopRef(0, 1, conn_id, self.known[conn_id], "web.example.test", 22, "target")
        return [HopTarget(ref, "john", "key", "SHA256:" + "A" * 43)]

    def plan_new(self, jump_id: str | None, target: HopTarget) -> list[HopTarget]:
        raise NotImplementedError

    async def connect(self, hops: list[HopTarget], prompter: Any) -> FakeChain:
        ref = hops[-1].ref
        self.connecting.set()
        await prompter.status("connecting", ref)
        try:
            if self.ask_hostkey:
                await prompter.hostkey_new(ref, "ssh-ed25519", "SHA256:" + "B" * 43)
            if self.block is not None:
                await self.block.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.fail is not None:
            raise self.fail
        await prompter.status("ready", ref)
        chain = FakeChain([ref])
        self.chains.append(chain)
        return chain
