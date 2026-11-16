"""Playwright helpers for the e2e specs (package 7).

Selectors follow docs/ux-spec.md copy (labels and button names), not CSS classes, so they keep
working while the frontend packages change markup.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page

from tests.fixtures import testbed as tb

#: localStorage key of the bearer token (portkey/static/js/api.js TOKEN_KEY).
TOKEN_KEY = "portkey.token"


def sign_in_with_token(page: Page, base_url: str, token: str) -> None:
    """Skip the sign-in screen: store ``token`` where the frontend keeps it, then reload.

    The first load must finish booting before the token is stored: a signed-out boot clears
    the stored token when /api/auth/status answers, which would otherwise race the write."""
    page.goto(base_url.rstrip("/") + "/")
    page.wait_for_selector("#login-title, .app .tabstrip [role=tab]")
    page.evaluate("([key, token]) => localStorage.setItem(key, token)", [TOKEN_KEY, token])
    page.reload()
    page.wait_for_selector(".app .tabstrip [role=tab]")


def stored_token(page: Page) -> str | None:
    value = page.evaluate("key => localStorage.getItem(key)", TOKEN_KEY)
    return str(value) if value is not None else None


def set_app_password_ui(page: Page, password: str = tb.APP_PASSWORD) -> None:
    """Fill in "Set a password for Port-Key" (first visit) and submit."""
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_label("Confirm password", exact=True).fill(password)
    page.get_by_role("button", name="Set password").click()


def sign_in_ui(page: Page, password: str = tb.APP_PASSWORD) -> None:
    """Fill in "Sign in to Port-Key" and submit."""
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()


def run_evil_origin(
    page: Page,
    evil_url: str,
    *,
    ws_url: str | None = None,
    api_url: str | None = None,
    token: str | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Open the evil-origin page and return what its attack achieved::

        {"origin": "http://evil-origin:8080",
         "ws": {"url", "opened", "error", "closeCode", "messages", "done", "timedOut"?},
         "fetch": {"url", "blocked", "status", "done"}, "done": true}

    A safe app gives ``ws.opened == False`` (handshake refused) and ``fetch.blocked == True``.
    """
    params = {"timeout": str(timeout_ms)}
    if ws_url:
        params["ws"] = ws_url
    if api_url:
        params["api"] = api_url
    if token:
        params["token"] = token
    page.goto(f"{evil_url.rstrip('/')}/?{urlencode(params)}")
    page.wait_for_function(
        "() => window.__evilResult && window.__evilResult.done", timeout=timeout_ms + 10_000
    )
    result: dict[str, Any] = json.loads(page.evaluate("JSON.stringify(window.__evilResult)"))
    return result
