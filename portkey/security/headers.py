"""Security response headers (package 1).

Adds to every HTTP response:
- ``Content-Security-Policy``: ``CSP`` below. xterm 5.5's DOM renderer injects ``<style>``
  elements, so ``style-src 'self' 'unsafe-inline'`` is expected to be required; package 1
  confirms this and documents it here.
- ``Referrer-Policy: no-referrer``, ``X-Content-Type-Options: nosniff``,
  ``Cache-Control: no-store`` on /api/* responses.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError("package 1")
