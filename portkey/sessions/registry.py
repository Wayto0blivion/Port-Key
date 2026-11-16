"""Session registry (package 5). Implements ``interfaces.SessionRegistry``.

Holds at most ``limits.max_sessions`` (32) sessions by id, reaps detached sessions after
``settings.detach_grace``, and records ``session_opened``/``session_closed`` events.
``start``/``aclose`` must stay safe to call before the rest is implemented: the app factory
calls them on startup and shutdown.
"""

from __future__ import annotations

from portkey.config import Limits
from portkey.interfaces import ConnectionsRepo, EventLog, Prompter, SettingsRepo, SshConnector
from portkey.models import SessionSummary
from portkey.sessions.terminal import TerminalSession


class MemorySessionRegistry:
    def __init__(
        self,
        connector: SshConnector,
        connections: ConnectionsRepo,
        settings: SettingsRepo,
        events: EventLog,
        limits: Limits,
    ) -> None:
        self.connector = connector
        self.connections = connections
        self.settings = settings
        self.events = events
        self.limits = limits

    def list_all(self) -> list[SessionSummary]:
        raise NotImplementedError("package 5")

    def get(self, session_id: str) -> TerminalSession:
        """Raises ``SessionGone``."""
        raise NotImplementedError("package 5")

    async def create(
        self, connection_id: str, cols: int, rows: int, prompter: Prompter, force_password: bool
    ) -> TerminalSession:
        """Plan, connect, start the shell, register. Raises ``TooManySessions``,
        ``UnknownConnection`` and everything ``SshConnector.connect`` raises."""
        raise NotImplementedError("package 5")

    async def kill(self, session_id: str) -> None:
        raise NotImplementedError("package 5")

    async def start(self) -> None:
        """Skeleton: no-op until package 5 adds the reaper."""

    async def aclose(self) -> None:
        """Skeleton: no-op until package 5 closes sessions here."""
