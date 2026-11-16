"""Host header allowlist and exact Origin check (package 1).

``HostGuardMiddleware`` is the outermost middleware, for HTTP and WebSocket scopes:
- Host must be one of ``config.allowed_hosts`` (case-insensitive) or the response is 421
  ``host_not_allowed`` (WebSocket: close before accept, which the client sees as HTTP 403).
- Every WebSocket handshake and every request whose method is not GET/HEAD/OPTIONS must carry
  an Origin from ``config.allowed_origins``; otherwise 403 ``origin_not_allowed`` (WebSocket:
  close before accept).
- Every such non-GET request must also carry ``X-Port-Key: 1`` (403 ``missing_csrf_header``) and
  ``Content-Type: application/json`` (415 ``unsupported_media_type``).
Error bodies use the standard envelope.

Stricter than the letter of docs/api.md, on purpose:
- A request with more than one Host, Origin, X-Port-Key or Content-Type header is refused as if
  the header were wrong (no first-value-wins ambiguity between proxies and this check).
- A GET/HEAD/OPTIONS that carries an Origin *not* on the list is refused too. A browser attaches
  a foreign Origin only when a foreign page made the request, so this never blocks Port-Key's
  own page; GETs without an Origin (navigations, same-origin fetches) pass.
- Origin is compared exactly (a browser always serialises it in lower case); ``null`` and
  values with a path or trailing slash never match.
"""

from __future__ import annotations

import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from portkey.config import Config
from portkey.errors import ErrorCode
from portkey.security.headers import security_headers

log = logging.getLogger("portkey.security")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
JSON_MEDIA_TYPE = "application/json"


def _allowed_hosts(config: Config) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in config.allowed_hosts)


def host_allowed(host_header: str | None, config: Config) -> bool:
    if not host_header:
        return False
    return host_header.lower() in _allowed_hosts(config)


def origin_allowed(origin_header: str | None, config: Config) -> bool:
    if not origin_header:
        return False
    return origin_header in {f"http://{h}" for h in _allowed_hosts(config)}


def _single(headers: Headers, name: str) -> str | None:
    """The header's value if it appears exactly once, else None."""
    values = headers.getlist(name)
    return values[0] if len(values) == 1 else None


def _json_content_type(value: str | None) -> bool:
    if value is None:
        return False
    return value.split(";", 1)[0].strip().lower() == JSON_MEDIA_TYPE


def _shown(value: str | None) -> str:
    """A header value safe to put in a log line: bounded and repr-escaped."""
    return "-" if value is None else repr(value[:100])


class HostGuardMiddleware:
    def __init__(self, app: ASGIApp, config: Config) -> None:
        self.app = app
        self.config = config

    def check(self, scope: Scope) -> tuple[int, str, str] | None:
        """``(status, code, message)`` for a request that must be refused, else None."""
        headers = Headers(scope=scope)
        host = _single(headers, "host")
        if not host_allowed(host, self.config):
            first = self.config.allowed_hosts[0] if self.config.allowed_hosts else "localhost"
            return (
                421,
                ErrorCode.HOST_NOT_ALLOWED,
                f"Port-Key does not answer on this address. Open http://{first}/ instead.",
            )
        origin_values = headers.getlist("origin")
        origin = _single(headers, "origin")
        is_ws = scope["type"] == "websocket"
        method = "GET" if is_ws else str(scope.get("method", "")).upper()
        mutating = is_ws or method not in SAFE_METHODS
        if (mutating or origin_values) and not origin_allowed(origin, self.config):
            return (
                403,
                ErrorCode.ORIGIN_NOT_ALLOWED,
                "This request came from another site, so Port-Key refused it.",
            )
        if is_ws or not mutating:
            return None
        if _single(headers, "x-port-key") != "1":
            return (
                403,
                ErrorCode.MISSING_CSRF_HEADER,
                "This request is missing the X-Port-Key header.",
            )
        if not _json_content_type(_single(headers, "content-type")):
            return (
                415,
                ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "Port-Key only accepts JSON (Content-Type: application/json).",
            )
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        refusal = self.check(scope)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, code, message = refusal
        headers = Headers(scope=scope)
        log.warning(
            "refused %s %s: %s (host=%s origin=%s)",
            "WS" if scope["type"] == "websocket" else scope.get("method", "?"),
            scope.get("path", "?")[:200],
            code,
            _shown(headers.get("host")),
            _shown(headers.get("origin")),
        )
        if scope["type"] == "websocket":
            # Closing before accept makes the server answer the handshake with HTTP 403; the
            # downstream app (and so the WebSocket handler) never runs.
            message_in = await receive()
            if message_in["type"] == "websocket.connect":
                await send({"type": "websocket.close", "code": 4403, "reason": ""})
            return
        response = JSONResponse(
            {"error": {"code": str(code), "message": message}},
            status_code=status,
            # A refusal is never cached, whatever the path.
            headers=dict(security_headers(api=True)),
        )
        await response(scope, receive, send)
