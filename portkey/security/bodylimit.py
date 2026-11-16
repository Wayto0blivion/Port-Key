"""Request body size cap (package 1).

uvicorn sets no limit on an HTTP request body, and FastAPI reads and parses the whole body
before any dependency runs, ``require_auth`` included. Without a cap, any local process that
fakes the headers a browser could not (all in scope, docs/architecture.md section 4) could
make the app hold gigabytes in memory without a token, and take every open terminal session
down with it. No real request comes near the cap: the biggest are a bulk edit of 1000 ids and
a reachability check of 500 ids, about 40 KB.

``BodyLimitMiddleware`` answers 413 ``payload_too_large``:
- at once, without reading anything, when ``Content-Length`` is over the cap, or when it is
  repeated or not a number (uvicorn already refuses most of those);
- as soon as the bytes received pass the cap when there is no ``Content-Length`` (chunked).
  Whatever the app tried to answer is dropped and the rest of the body is never read.
WebSocket frames are capped separately (uvicorn's ``ws_max_size``).
"""

from __future__ import annotations

import contextlib
import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from portkey.errors import ErrorCode
from portkey.security.headers import security_headers

log = logging.getLogger("portkey.security")

TOO_LARGE_MESSAGE = "That request is too large for Port-Key."


class _BodyTooLarge(Exception):
    """Raised from ``receive`` once the body passes the cap; caught by the middleware."""


def declared_length(headers: Headers) -> int | None:
    """``Content-Length`` as a number; None when absent; -1 when repeated or not a number."""
    values = headers.getlist("content-length")
    if not values:
        return None
    if len(values) != 1 or not values[0].strip().isdigit():
        return -1
    return int(values[0].strip())


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = declared_length(Headers(scope=scope))
        if length is not None and (length < 0 or length > self.max_bytes):
            await self._refuse(scope, receive, send)
            return

        received = 0
        too_large = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_large and not started:
                return  # the app's answer to a body it never fully got; ours replaces it
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, guarded_send)
        if too_large and not started:
            await self._refuse(scope, receive, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        log.warning(
            "refused %s %s: %s",
            scope.get("method", "?"),
            str(scope.get("path", "?"))[:200],
            ErrorCode.PAYLOAD_TOO_LARGE,
        )
        response = JSONResponse(
            {"error": {"code": str(ErrorCode.PAYLOAD_TOO_LARGE), "message": TOO_LARGE_MESSAGE}},
            status_code=413,
            headers={**dict(security_headers(api=True)), "Connection": "close"},
        )
        await response(scope, receive, send)
