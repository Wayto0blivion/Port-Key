"""SSH and WebSocket error hierarchy, stable error codes and WebSocket close codes.

Frozen contract (docs/ws-protocol.md). Package 4 raises these from the SSH core; packages 5 and 6
catch them in the WebSocket handlers, send ``{"type":"error","code","message",...}`` and close the
socket with ``close_code``. ``code`` values never change once released; only add new ones.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any, ClassVar

from portkey.errors import PortKeyError
from portkey.models import HopRef


class CloseCode(IntEnum):
    """WebSocket close codes sent by /ws/terminal and /ws/install."""

    NORMAL = 1000  # exit, done, client close, detached elsewhere, user cancel
    TOO_BIG = 1009  # frame over 64 KB (sent by the websocket layer)
    INTERNAL = 1011  # unexpected server error
    BAD_MESSAGE = 4400  # malformed or out-of-order client message
    AUTH = 4401  # auth missing, wrong or not sent within 5 s
    ORIGIN = 4403  # Origin not allowed (normally rejected before accept as HTTP 403)
    UNKNOWN_CONNECTION = 4404  # connection_id (or jump_id) not found
    PROMPT_TIMEOUT = 4408  # a prompt went unanswered for 120 s
    HOSTKEY_CHANGED = 4409  # host key changed and not replaced
    SESSION_GONE = 4410  # attach to a session that no longer exists
    TOO_MANY_SESSIONS = 4429  # 32 sessions already open
    SSH_FAILURE = 4502  # any SshError below


class WsErrorCode(StrEnum):
    """Every ``error.code`` a WebSocket can send."""

    UNAUTHORIZED = "unauthorized"
    AUTH_TIMEOUT = "auth_timeout"
    BAD_MESSAGE = "bad_message"
    NOT_FOUND = "not_found"
    PROMPT_TIMEOUT = "prompt_timeout"
    CANCELLED = "cancelled"
    HOSTKEY_CHANGED = "hostkey_changed"
    SESSION_GONE = "session_gone"
    TOO_MANY_SESSIONS = "too_many_sessions"
    INTERNAL = "internal"
    # SSH failures (close 4502)
    DNS = "dns"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"
    PASSWORD_NOT_ALLOWED = "password_not_allowed"
    PASSWORD_EXPIRED = "password_expired"
    KEY_LOCKED = "key_locked"
    KEY_UNREADABLE = "key_unreadable"
    HOSTKEY_REVOKED = "hostkey_revoked"
    HOSTKEY_REJECTED = "hostkey_rejected"
    PROTOCOL = "protocol"
    JUMP_FAILED = "jump_failed"


class WsError(PortKeyError):
    """Base for errors that end a WebSocket. ``close_code`` is sent after the error message."""

    close_code: ClassVar[int] = CloseCode.INTERNAL
    default_code = WsErrorCode.INTERNAL

    def __init__(
        self, message: str | None = None, *, hop: HopRef | None = None, **extra: Any
    ) -> None:
        super().__init__(message, **extra)
        self.hop = hop


class BadMessage(WsError):
    close_code = CloseCode.BAD_MESSAGE
    default_code = WsErrorCode.BAD_MESSAGE
    default_message = "Port-Key did not understand a message from the browser."


class WsUnauthorized(WsError):
    close_code = CloseCode.AUTH
    default_code = WsErrorCode.UNAUTHORIZED
    default_message = "Sign in to Port-Key."


class UnknownConnection(WsError):
    close_code = CloseCode.UNKNOWN_CONNECTION
    default_code = WsErrorCode.NOT_FOUND
    default_message = "That connection no longer exists."


class PromptTimeout(WsError):
    close_code = CloseCode.PROMPT_TIMEOUT
    default_code = WsErrorCode.PROMPT_TIMEOUT
    default_message = "No answer within 2 minutes, so Port-Key closed the connection."


class PromptCancelled(WsError):
    """The user answered a prompt with ``{"cancel": true}`` or sent ``close`` while connecting."""

    close_code = CloseCode.NORMAL
    default_code = WsErrorCode.CANCELLED
    default_message = "Cancelled."


class SessionGone(WsError):
    close_code = CloseCode.SESSION_GONE
    default_code = WsErrorCode.SESSION_GONE
    default_message = "That session has ended."


class TooManySessions(WsError):
    close_code = CloseCode.TOO_MANY_SESSIONS
    default_code = WsErrorCode.TOO_MANY_SESSIONS
    default_message = "Port-Key already has 32 sessions open. Close one and try again."


# --- SSH failures -----------------------------------------------------------------------------


class SshError(WsError):
    """An SSH-level failure. ``hop`` says which hop of a jump chain failed."""

    close_code = CloseCode.SSH_FAILURE
    default_code = WsErrorCode.PROTOCOL
    default_message = "The SSH connection failed."


class DnsError(SshError):
    default_code = WsErrorCode.DNS


class ConnectionRefused(SshError):
    default_code = WsErrorCode.REFUSED


class ConnectTimeout(SshError):
    default_code = WsErrorCode.TIMEOUT


class NetworkUnreachable(SshError):
    default_code = WsErrorCode.UNREACHABLE


class AuthFailed(SshError):
    """All allowed methods failed. ``methods`` lists what the server offered."""

    default_code = WsErrorCode.AUTH_FAILED

    def __init__(
        self, message: str | None = None, *, methods: list[str] | None = None, **kw: Any
    ) -> None:
        super().__init__(message, methods=methods or [], **kw)
        self.methods = methods or []


class PasswordNotAllowed(SshError):
    default_code = WsErrorCode.PASSWORD_NOT_ALLOWED


class PasswordExpired(SshError):
    default_code = WsErrorCode.PASSWORD_EXPIRED


class KeyLocked(SshError):
    """The key is encrypted and no passphrase was obtained (3 wrong passphrases)."""

    default_code = WsErrorCode.KEY_LOCKED


class KeyUnreadable(SshError):
    default_code = WsErrorCode.KEY_UNREADABLE


class HostKeyRevoked(SshError):
    default_code = WsErrorCode.HOSTKEY_REVOKED


class HostKeyRejected(SshError):
    """The user declined a first-seen host key (``hostkey_new`` answered with accept=false)."""

    close_code = CloseCode.NORMAL
    default_code = WsErrorCode.HOSTKEY_REJECTED


class HostKeyChanged(SshError):
    """Changed host key not replaced (declined or typed name mismatch). Nothing was sent."""

    close_code = CloseCode.HOSTKEY_CHANGED
    default_code = WsErrorCode.HOSTKEY_CHANGED


class ProtocolError(SshError):
    default_code = WsErrorCode.PROTOCOL


class JumpFailed(SshError):
    """A jump hop failed. ``cause`` is the hop's own error; ``hop`` is the failing hop."""

    default_code = WsErrorCode.JUMP_FAILED

    def __init__(self, cause: SshError, *, hop: HopRef | None = None) -> None:
        super().__init__(cause.message, hop=hop or cause.hop, cause=cause.code)
        self.cause = cause


def close_code_for(exc: BaseException) -> int:
    """Close code to use after reporting ``exc`` on a WebSocket."""
    if isinstance(exc, WsError):
        return int(exc.close_code)
    return int(CloseCode.INTERNAL)
