"""One terminal session (package 5).

Owns the ``SshChain``, the PTY channel on the target (``term_type="xterm-256color"``,
``encoding=None``), the ``RingBuffer`` and at most one attached WebSocket sink. Output is batched
for about 16 ms or 64 KB per frame. When the WebSocket drops, the session stays alive detached
for ``settings.detach_grace``; a new attach takes over and the old sink gets ``detached``. When
SSH dies the session ends with ``exit`` (reason ``closed`` or ``lost``) and is removed. Terminal
input and output are never logged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from portkey.interfaces import SshChain
from portkey.models import SessionSummary


class SessionSink(Protocol):
    """Where an attached session sends things; implemented in ``api.ws_terminal``."""

    async def output(self, data: bytes) -> None: ...

    async def exited(
        self,
        exit_status: int | None,
        signal: str | None,
        reason: Literal["closed", "lost", "killed"],
    ) -> None: ...

    async def detached(self) -> None: ...


class TerminalSession:
    def __init__(
        self,
        session_id: str,
        connection_id: str,
        chain: SshChain,
        buffer_bytes: int,
        started_at: datetime,
    ) -> None:
        self.session_id = session_id
        self.connection_id = connection_id
        self.chain = chain
        self.buffer_bytes = buffer_bytes
        self.started_at = started_at

    async def start(self, cols: int, rows: int) -> None:
        """Open the PTY shell and start pumping output."""
        raise NotImplementedError("package 5")

    async def attach(self, sink: SessionSink, since: int | None) -> tuple[int, bool]:
        """Attach ``sink`` (detaching any previous one with ``detached``). Returns
        ``(offset, reset)`` for the ``ready`` message, then replays from ``offset``."""
        raise NotImplementedError("package 5")

    def detach(self, sink: SessionSink) -> None:
        """Called when ``sink``'s WebSocket drops; starts the detach-grace timer."""
        raise NotImplementedError("package 5")

    async def write(self, data: bytes) -> None:
        raise NotImplementedError("package 5")

    async def resize(self, cols: int, rows: int) -> None:
        """Clamped to 1..1000."""
        raise NotImplementedError("package 5")

    async def close(self, reason: Literal["closed", "lost", "killed"] = "killed") -> None:
        raise NotImplementedError("package 5")

    def summary(self) -> SessionSummary:
        raise NotImplementedError("package 5")
