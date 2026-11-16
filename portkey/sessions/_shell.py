"""The PTY shell channel of a terminal session (package 5).

``open_shell`` asks the target for an interactive shell on a PTY (``xterm-256color``, bytes mode)
and forwards asyncssh's session callbacks to a ``ShellEvents`` (the ``TerminalSession``).
``ShellChannel`` names the few channel methods the session uses, so tests can stand in a fake.
Nothing here logs terminal data.
"""

from __future__ import annotations

from typing import Any, Protocol

import asyncssh

from portkey.models import HopRef
from portkey.ssh.errors import ProtocolError

TERM_TYPE = "xterm-256color"
#: Input buffered for the remote before ``write`` waits (a large paste into a stalled remote).
INPUT_BUFFER_HIGH = 256 * 1024


class ShellChannel(Protocol):
    def write(self, data: bytes) -> None: ...

    def change_terminal_size(self, width: int, height: int) -> None: ...

    def pause_reading(self) -> None: ...

    def resume_reading(self) -> None: ...

    def close(self) -> None: ...


class ShellEvents(Protocol):
    def on_data(self, data: bytes) -> None: ...

    def on_exit_status(self, status: int) -> None: ...

    def on_exit_signal(self, signal: str) -> None: ...

    def on_closed(self, exc: Exception | None) -> None: ...

    def on_pause_writing(self) -> None: ...

    def on_resume_writing(self) -> None: ...


class _ShellSession(asyncssh.SSHClientSession[bytes]):
    def __init__(self, events: ShellEvents) -> None:
        self._events = events

    def data_received(self, data: bytes, datatype: Any) -> None:
        self._events.on_data(data)  # stdout and stderr share the PTY

    def exit_status_received(self, status: int) -> None:
        self._events.on_exit_status(status)

    def exit_signal_received(self, signal: str, core_dumped: bool, msg: str, lang: str) -> None:
        self._events.on_exit_signal(signal)

    def connection_lost(self, exc: Exception | None) -> None:
        self._events.on_closed(exc)

    def pause_writing(self) -> None:
        self._events.on_pause_writing()

    def resume_writing(self) -> None:
        self._events.on_resume_writing()


async def open_shell(
    conn: Any, events: ShellEvents, cols: int, rows: int, hop: HopRef
) -> ShellChannel:
    """Start the login shell on ``conn`` (an ``asyncssh.SSHClientConnection`` or a test fake).
    A refusal maps to ``ProtocolError`` naming the hop."""
    try:
        chan, _ = await conn.create_session(
            lambda: _ShellSession(events),
            term_type=TERM_TYPE,
            term_size=(cols, rows),
            encoding=None,
        )
    except (asyncssh.Error, OSError) as exc:
        raise ProtocolError(
            f"{hop.name} accepted the sign-in but would not start a shell ({type(exc).__name__}).",
            hop=hop,
        ) from None
    if hasattr(chan, "set_write_buffer_limits"):
        chan.set_write_buffer_limits(high=INPUT_BUFFER_HIGH)
    channel: ShellChannel = chan
    return channel
