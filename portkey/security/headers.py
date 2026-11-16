"""Security response headers (package 1).

Adds to every HTTP response:
- ``Content-Security-Policy``: ``CSP`` below.
- ``Referrer-Policy: no-referrer``, ``X-Content-Type-Options: nosniff``, ``X-Frame-Options:
  DENY``, ``Cross-Origin-Opener-Policy: same-origin``, ``Cross-Origin-Resource-Policy:
  same-origin``, a ``Permissions-Policy`` that turns off device APIs, and ``Cache-Control:
  no-store`` on /api/* responses.

CSP notes (checked against the vendored @xterm/xterm 5.5.0 in ``static/vendor/xterm``):
- ``style-src 'unsafe-inline'`` is required. The DOM renderer creates two ``<style>`` elements
  at runtime (``_dimensionsStyleElement`` and ``_themeStyleElement``, filled with per-instance
  rules) and sets ``style`` attributes with ``setAttribute("style", ...)`` for 24-bit colours.
  Both are inline styles under CSP; their content changes with the font size and theme, so
  hashes cannot cover them, and xterm offers no nonce hook. CSSOM writes (``el.style.x = ...``)
  are not restricted by CSP and need nothing. ``tests/unit/security/test_headers.py`` fails if
  a future xterm stops injecting styles, so the exception can then be removed.
- xterm uses no ``eval``/``new Function``, workers or ``data:`` URLs, so ``script-src 'self'``
  stays strict, and no ``'unsafe-eval'``, ``worker-src`` or ``data:`` sources are granted.
- ``default-src 'none'`` (rather than ``'self'``) so anything not listed (frames, media,
  objects, workers, manifests) is refused. ``connect-src 'self'`` also covers same-origin
  ``ws://`` in CSP Level 3 browsers.
- ``img-src 'self'``: neither xterm nor the frontend uses ``data:`` images (inline ``<svg>``
  elements are not images and need no source). ``base-uri``/``form-action``/``frame-ancestors``
  are ``'none'``; the frontend posts with ``fetch``, never a ``<form>``.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)

PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), usb=(), serial=(), hid=(), payment=(), "
    "display-capture=()"
)

BASE_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CSP),
    ("referrer-policy", "no-referrer"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
    ("permissions-policy", PERMISSIONS_POLICY),
)


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def security_headers(api: bool) -> list[tuple[str, str]]:
    """Every header this module sets, for ``api`` (no-store) or other responses."""
    headers = list(BASE_HEADERS)
    if api:
        headers.append(("cache-control", "no-store"))
    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(is_api_path(scope.get("path", "")))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", ()))
                headers = MutableHeaders(scope=message)
                for name, value in extra:
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
