"""Log redaction and the 422 handler (package 1).

- ``install_log_redaction()`` sets the ``asyncssh`` logger to WARNING and attaches
  ``RedactingFilter`` to the root and uvicorn loggers as a backstop (bearer tokens, anything
  that looks like a private key block, ``password=``/``passphrase=`` pairs).
- ``validation_exception_handler`` replaces FastAPI's default 422 handler, which echoes the
  request ``input``; it returns ``{"error": {"code": "validation", "message", "fields":
  [{loc, msg, type}]}}`` with no input values anywhere.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        raise NotImplementedError("package 1")


def install_log_redaction() -> None:
    raise NotImplementedError("package 1")


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    raise NotImplementedError("package 1")
