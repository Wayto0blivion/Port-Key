"""Base exception hierarchy and the stable HTTP error codes.

Every error the API returns has the shape ``{"error": {"code", "message", ...extra}}``. Raise a
``PortKeyError`` subclass anywhere (store, keys, routes); the handler in ``app.py`` turns it into
that envelope with the class's HTTP status. ``code`` values are part of the wire contract
(docs/api.md): never rename one, only add.

SSH-specific errors and WebSocket close codes live in ``portkey.ssh.errors`` and subclass
``PortKeyError`` too.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar


class ErrorCode(StrEnum):
    """Stable ``error.code`` values for the HTTP API."""

    # Generic
    INTERNAL = "internal"
    NOT_IMPLEMENTED = "not_implemented"  # 501, only while a package is unfinished
    VALIDATION = "validation"  # 422 from request validation; carries ``fields``
    NOT_FOUND = "not_found"
    # Security (package 1)
    HOST_NOT_ALLOWED = "host_not_allowed"  # 421
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"  # 403
    MISSING_CSRF_HEADER = "missing_csrf_header"  # 403, X-Port-Key: 1 absent
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"  # 415, non-JSON mutating request
    UNAUTHORIZED = "unauthorized"  # 401, token missing/expired/revoked
    BAD_PASSWORD = "bad_password"  # 401 on login and password change only (not a sign-out)
    RATE_LIMITED = "rate_limited"  # 429, carries ``retry_after``
    ALREADY_SETUP = "already_setup"  # 409
    NOT_SETUP = "not_setup"  # 409, login before a password exists
    BAD_SETUP_CODE = "bad_setup_code"  # 403, wrong first-visit setup code (shares the backoff)
    SETUP_NEEDS_RESTART = "setup_needs_restart"  # 409, no code held (password reset while running)
    # Connections, folders, tags (package 2)
    NAME_TAKEN = "name_taken"  # 409
    JUMP_IN_USE = "jump_in_use"  # 409, carries ``dependants``
    JUMP_CYCLE = "jump_cycle"  # 422
    JUMP_TOO_DEEP = "jump_too_deep"  # 422
    JUMP_UNKNOWN = "jump_unknown"  # 422
    GROUP_UNKNOWN = "group_unknown"  # 422
    GROUP_TOO_DEEP = "group_too_deep"  # 422
    GROUP_CYCLE = "group_cycle"  # 422, moving a folder under itself or a descendant
    TAG_INVALID = "tag_invalid"  # 422
    # Keys and hosts (package 3)
    KEY_UNKNOWN = "key_unknown"  # 404 on /api/keys/{id}, 422 when referenced by a connection
    KEY_NAME_TAKEN = "key_name_taken"  # 409
    KEY_NAME_INVALID = "key_name_invalid"  # 422
    KEY_IN_USE = "key_in_use"  # 409 when hiding a used key without ``reassign``
    KEY_NOT_ENCRYPTED = "key_not_encrypted"  # 409 on unlock of a plain key
    BAD_PASSPHRASE = "bad_passphrase"  # 400
    KEY_UNREADABLE = "key_unreadable"  # 422
    CONFIRM_MISMATCH = "confirm_mismatch"  # 422, typed host name does not match
    # Sessions (package 5)
    SESSION_GONE = "session_gone"  # 404


class PortKeyError(Exception):
    """Base for every error that maps to an API error envelope.

    ``extra`` keys are copied into the envelope beside ``code`` and ``message`` and must be
    JSON-serialisable and free of secrets.
    """

    status: ClassVar[int] = 500
    default_code: ClassVar[str] = ErrorCode.INTERNAL
    default_message: ClassVar[str] = "Something went wrong inside Port-Key."

    def __init__(self, message: str | None = None, *, code: str | None = None, **extra: Any):
        self.code: str = str(code or self.default_code)
        self.message: str = message or self.default_message
        self.extra: dict[str, Any] = extra
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        body.update({k: v for k, v in self.extra.items() if v is not None})
        return body


class BadRequest(PortKeyError):
    status = 400
    default_code = ErrorCode.VALIDATION
    default_message = "The request is not valid."


class Unauthorized(PortKeyError):
    status = 401
    default_code = ErrorCode.UNAUTHORIZED
    default_message = "Sign in to Port-Key."


class Forbidden(PortKeyError):
    status = 403
    default_code = ErrorCode.ORIGIN_NOT_ALLOWED
    default_message = "This request is not allowed."


class NotFound(PortKeyError):
    status = 404
    default_code = ErrorCode.NOT_FOUND
    default_message = "Not found."


class Conflict(PortKeyError):
    status = 409
    default_code = ErrorCode.NAME_TAKEN
    default_message = "That conflicts with something that already exists."


class Invalid(PortKeyError):
    """Semantically invalid input that passed schema validation (422)."""

    status = 422
    default_code = ErrorCode.VALIDATION
    default_message = "The request is not valid."


class RateLimited(PortKeyError):
    status = 429
    default_code = ErrorCode.RATE_LIMITED
    default_message = "Too many attempts. Try again later."

    def __init__(self, retry_after: int, message: str | None = None):
        super().__init__(
            message or f"Too many attempts. Try again in {retry_after} seconds.",
            retry_after=retry_after,
        )
        self.retry_after = retry_after
