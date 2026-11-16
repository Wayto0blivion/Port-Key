"""Wizard-specific WebSocket errors (package 6)."""

from __future__ import annotations

from portkey.errors import PortKeyError
from portkey.ssh.errors import CloseCode, WsError, WsErrorCode


class InputRejected(WsError):
    """The ``start`` message passed the schema but cannot be saved: the name is taken, the
    folder is gone, the jump would make a loop or a chain that is too deep, a tag is invalid.
    ``code`` is the HTTP API's code for the same problem (``name_taken``, ``group_unknown``,
    ``jump_cycle``, ``jump_too_deep``, ``tag_invalid``, ``validation``); close 4400."""

    close_code = CloseCode.BAD_MESSAGE
    default_code = WsErrorCode.BAD_MESSAGE
    default_message = "Port-Key can't save a connection with those settings."

    @classmethod
    def from_error(cls, exc: PortKeyError) -> InputRejected:
        return cls(exc.message, code=exc.code)
