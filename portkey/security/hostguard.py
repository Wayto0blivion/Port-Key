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
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from portkey.config import Config


def host_allowed(host_header: str | None, config: Config) -> bool:
    raise NotImplementedError("package 1")


def origin_allowed(origin_header: str | None, config: Config) -> bool:
    raise NotImplementedError("package 1")


class HostGuardMiddleware:
    def __init__(self, app: ASGIApp, config: Config) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError("package 1")
