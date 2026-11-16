"""Session registry (package 5). Implements ``interfaces.SessionRegistry``.

Holds at most ``limits.max_sessions`` (32) live sessions by id, reaps detached sessions after
``settings.detach_grace``, and records ``session_opened``/``session_closed`` events.
``start``/``aclose`` are safe to call at any time: the app factory calls them on startup and
shutdown.

- ``create`` reserves a slot before connecting, so parallel opens cannot exceed the cap, plans
  the chain in a thread (SQLite and key-folder scans), connects through the caller's prompter,
  starts the shell and registers the session. ``touch_used`` and the events run in a thread in
  the background, so a busy database never delays ``ready`` or stalls the event loop.
- A session that ended while detached (the shell exited, SSH was lost) stays registered, not
  listed and not counted, so a reattach still gets its last output and ``exit``; it is dropped
  once that exit is delivered, on ``kill``, or by the reaper after the grace.
- The reaper runs every ``reap_interval`` seconds; ``reap_once`` is the step, driven by the
  injected monotonic ``clock`` (tests use a fake one).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from portkey.config import Limits
from portkey.errors import ErrorCode, NotFound
from portkey.interfaces import ConnectionsRepo, EventLog, Prompter, SettingsRepo, SshConnector
from portkey.models import ConnectionRecord, SessionSummary
from portkey.sessions.terminal import TerminalSession, utcnow
from portkey.ssh.errors import SessionGone, TooManySessions

log = logging.getLogger("portkey.sessions")

REAP_INTERVAL = 5.0
SHUTDOWN_GRACE = 2.0


def new_session_id() -> str:
    return "s_" + secrets.token_urlsafe(16)


class MemorySessionRegistry:
    def __init__(
        self,
        connector: SshConnector,
        connections: ConnectionsRepo,
        settings: SettingsRepo,
        events: EventLog,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = utcnow,
        reap_interval: float = REAP_INTERVAL,
    ) -> None:
        self.connector = connector
        self.connections = connections
        self.settings = settings
        self.events = events
        self.limits = limits
        self.clock = clock
        self.wall = wall
        self.reap_interval = reap_interval
        self._sessions: dict[str, TerminalSession] = {}
        self._connecting = 0
        self._reaper: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()

    # --- queries -------------------------------------------------------------------------------

    def list_all(self) -> list[SessionSummary]:
        """Live sessions, oldest first (ended ones waiting for a reattach are left out)."""
        live = [s for s in self._sessions.values() if not s.ended]
        return [s.summary() for s in sorted(live, key=lambda s: s.started_at)]

    def live_count(self) -> int:
        return sum(1 for s in self._sessions.values() if not s.ended)

    def get(self, session_id: str) -> TerminalSession:
        """Raises ``SessionGone``."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionGone()
        return session

    # --- open ----------------------------------------------------------------------------------

    async def create(
        self, connection_id: str, cols: int, rows: int, prompter: Prompter, force_password: bool
    ) -> TerminalSession:
        """Plan, connect, start the shell, register. Raises ``TooManySessions``,
        ``UnknownConnection`` and everything ``SshConnector.connect`` raises."""
        if self.live_count() + self._connecting >= self.limits.max_sessions:
            raise TooManySessions()
        self._connecting += 1
        try:
            hops = await asyncio.to_thread(self.connector.plan, connection_id, force_password)
            chain = await self.connector.connect(hops, prompter)
            session = TerminalSession(
                new_session_id(),
                connection_id,
                chain,
                self.limits.ring_buffer_bytes,
                self.wall(),
                limits=self.limits,
                clock=self.clock,
                wall=self.wall,
                on_end=self._ended,
                on_delivered=self._delivered,
            )
            try:
                await session.start(cols, rows)
            except BaseException:
                with contextlib.suppress(Exception):
                    await chain.close()  # never opened: no exit, no session_closed event
                raise
        finally:
            self._connecting -= 1
        self._sessions[session.session_id] = session
        target = hops[-1].ref
        log.info("session %s opened to hop %s:%d", session.session_id, target.host, target.port)
        self._background_task(self._record_open(session, target.host, target.port))
        return session

    async def connection_of(self, session: TerminalSession) -> ConnectionRecord | None:
        """The session's saved connection now (renamed or recoloured since?), or the one from
        open time when it has been deleted since."""
        found = await asyncio.to_thread(self.connections.find, session.connection_id)
        return found or session.connection

    async def _record_open(self, session: TerminalSession, host: str, port: int) -> None:
        def record() -> None:
            session.connection = self.connections.find(session.connection_id)
            self.connections.touch_used(session.connection_id)
            self.events.record("session_opened", session.connection_id, host=host, port=port)

        await self._in_thread(record, "record the opened session")

    # --- close ---------------------------------------------------------------------------------

    async def kill(self, session_id: str) -> None:
        """Close the shell and every hop. Raises ``NotFound(session_gone)``."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise NotFound("That session has ended.", code=ErrorCode.SESSION_GONE)
        await session.close("killed")

    def _ended(self, session: TerminalSession) -> None:
        assert session.exit is not None
        outcome = session.exit.reason
        self._background_task(
            self._in_thread(
                lambda: self.events.record(
                    "session_closed", session.connection_id, outcome=outcome
                ),
                "record the closed session",
            )
        )

    def _delivered(self, session: TerminalSession) -> None:
        if self._sessions.get(session.session_id) is session:
            del self._sessions[session.session_id]

    # --- reaper --------------------------------------------------------------------------------

    async def reap_once(self) -> list[str]:
        """Close sessions detached for at least ``detach_grace``; return their ids."""
        settings = await asyncio.to_thread(self.settings.get)
        grace = float(settings.detach_grace)

        def expired(s: TerminalSession) -> bool:
            return s.detached_since is not None and self.clock() - s.detached_since >= grace

        reaped: list[str] = []
        for session in [s for s in self._sessions.values() if expired(s)]:
            # Closing a chain can take seconds per hop: a session reattached meanwhile (the
            # owner reopened the page) is checked again here, right before it would be closed.
            if self._sessions.get(session.session_id) is not session or not expired(session):
                continue
            del self._sessions[session.session_id]
            log.info("session %s reaped after %d s detached", session.session_id, int(grace))
            reaped.append(session.session_id)
            await session.close("killed")
        return reaped

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(self.reap_interval)
            try:
                await self.reap_once()
            except Exception as exc:  # keep reaping; the next round retries
                log.warning("session reaper failed (%s)", type(exc).__name__)

    async def start(self) -> None:
        """Start the detach-grace reaper. Calling it again does nothing."""
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_forever())

    async def aclose(self) -> None:
        """Close every session and stop the reaper. Safe to call more than once."""
        if self._reaper is not None:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
            self._reaper = None
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(_close_quietly(s) for s in sessions))
        pending = [t for t in self._background if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=SHUTDOWN_GRACE)
        for session in sessions:
            session.cancel_tasks()
        for task in list(self._background):
            task.cancel()

    # --- helpers -------------------------------------------------------------------------------

    async def _in_thread(self, fn: Callable[[], None], what: str) -> None:
        try:
            await asyncio.to_thread(fn)
        except Exception as exc:  # bookkeeping must never end a session
            log.warning("could not %s (%s)", what, type(exc).__name__)

    def _background_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


async def _close_quietly(session: TerminalSession) -> None:
    with contextlib.suppress(Exception):
        await session.close("killed")
