"""Log redaction and the 422 handler (package 1).

- ``install_log_redaction()`` sets the ``asyncssh`` logger to WARNING and attaches
  ``RedactingFilter`` to the root and uvicorn loggers as a backstop (bearer tokens, anything
  that looks like a private key block, ``password=``/``passphrase=`` pairs).
- ``validation_exception_handler`` replaces FastAPI's default 422 handler, which echoes the
  request ``input``; it returns ``{"error": {"code": "validation", "message", "fields":
  [{loc, msg, type}]}}`` with no input values anywhere.

A filter on a logger only sees records logged on that exact logger, not ones propagating from
children (``portkey.ssh`` -> root handlers). So the filter is also installed as a wrapper around
the log record factory, which every record from every logger passes through.

This is a backstop: code must still never log secrets. It cannot recognise a bare token or
password with no label beside it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator, Mapping
from itertools import islice
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from portkey.errors import ErrorCode

REDACTED = "[redacted]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_BEARER = re.compile(r"\b(bearer)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_QUOTED = r""""(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'"""
_PAIR = re.compile(
    r"\b(password|passphrase|passwd|pwd|secret|token|authorization|otp|api[_-]?key)\b"
    r"""(["']?\s*[:=]\s*)"""
    # Not "[redacted]" again, so redact() is idempotent.
    rf"""(?!\[redacted)({_QUOTED}|[^\s,;&}}\]"']+)""",
    re.IGNORECASE,
)
# Fields of prompt replies and the password-change body, as they look in JSON or a dict repr.
_REPLY_FIELD = re.compile(
    rf"""(["'](?:value|values|old|new)["']\s*:\s*)({_QUOTED}|\[[^\]]*\])""",
)


def redact(text: str) -> str:
    text = _PRIVATE_KEY.sub("[redacted private key]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _REPLY_FIELD.sub(lambda m: f"{m.group(1)}{REDACTED}", text)


def _redact_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(redact(a) if isinstance(a, str) else a for a in args)
    if isinstance(args, Mapping):
        return {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
    return args


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A record whose args do not fit its format is logging's problem to report.
            return True
        if redact(message) != message:
            # Prefer redacting the arguments, which keeps the record's shape for formatters that
            # read ``args`` (uvicorn's access log); fall back to a pre-rendered message.
            args = _redact_args(record.args)
            try:
                rendered = record.msg % args if args else str(record.msg)
            except (TypeError, ValueError, KeyError):
                rendered = None
            if rendered is not None and redact(rendered) == rendered:
                record.args = args
            else:
                record.msg = redact(rendered if rendered is not None else message)
                record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


_FILTER = RedactingFilter()
_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


def _wrap_factory(factory: Callable[..., logging.LogRecord]) -> Callable[..., logging.LogRecord]:
    def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        _FILTER.filter(record)
        return record

    redacting_factory.portkey_redacting = True  # type: ignore[attr-defined]
    return redacting_factory


def install_log_redaction() -> None:
    """Idempotent; safe to call once per app instance."""
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
    current = logging.getLogRecordFactory()
    if not getattr(current, "portkey_redacting", False):
        logging.setLogRecordFactory(_wrap_factory(current))


# --- 422 ------------------------------------------------------------------------------------

_MAX_LEAVES = 200


def _string_leaves(value: Any, depth: int = 0) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif depth < 4 and isinstance(value, Mapping):
        for item in value.values():
            yield from _string_leaves(item, depth + 1)
    elif depth < 4 and isinstance(value, list | tuple):
        for item in value:
            yield from _string_leaves(item, depth + 1)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield str(value)


def _scrub(msg: str, value: Any) -> str:
    """Remove any part of the submitted value that a message quotes back (custom validators
    may put the value in their text). Very short values are left: they are not secrets and
    replacing them would mangle the message."""
    leaves = sorted(
        {s for s in islice(_string_leaves(value), _MAX_LEAVES) if len(s) >= 3},
        key=len,
        reverse=True,
    )
    for leaf in leaves:
        msg = msg.replace(leaf, REDACTED)
    return redact(msg)


def _field(error: Mapping[str, Any]) -> dict[str, Any]:
    loc = [part if isinstance(part, int) else str(part) for part in error.get("loc", ())]
    return {
        "loc": loc,
        "msg": _scrub(str(error.get("msg", "")), error.get("input")),
        "type": str(error.get("type", "")),
    }


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Registered for ``RequestValidationError`` (typed ``Exception`` to match Starlette's
    handler signature)."""
    assert isinstance(exc, RequestValidationError)
    fields = [_field(error) for error in exc.errors()]
    body = {
        "error": {
            "code": str(ErrorCode.VALIDATION),
            "message": "The request is not valid.",
            "fields": fields,
        }
    }
    return JSONResponse(body, status_code=422)
