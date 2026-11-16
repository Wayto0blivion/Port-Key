"""Shared helpers for the e2e specs (package 9): problem watching, API setup, terminal driving.

Specs drive the UI with Playwright against the real ``app`` container and the sshd test bed;
these helpers do the setup around it through the real HTTP API, and keep every spec
rerunnable against a running stack (create by name if missing, reset what a test changed).
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from playwright.sync_api import Locator, Page, Response, expect

from tests.e2e.harness.app_api import AppApi
from tests.fixtures import testbed as tb

#: Reported as console errors by ``CSP_WATCH`` so a violation fails the spec.
CSP_WATCH = """document.addEventListener('securitypolicyviolation', (e) => {
  console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`);
});"""


class Watch:
    """Page errors, console errors (CSP violations included, once ``CSP_WATCH`` is installed)
    and every /api response of 400 or more. ``check(*allowed)`` fails on anything else."""

    def __init__(self, page: Page) -> None:
        self.errors: list[str] = []
        self.failures: list[tuple[int, str, str]] = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", self._console)
        page.on("response", self._response)

    def _console(self, msg: Any) -> None:
        # A failed HTTP status is logged as "Failed to load resource"; judged by ``failures``.
        if msg.type != "error" or msg.text.startswith("Failed to load resource"):
            return
        self.errors.append(f"console: {msg.text}")

    def _response(self, response: Response) -> None:
        path = response.url.split("://", 1)[-1].split("/", 1)[-1]
        if path.startswith("api/") and response.status >= 400:
            self.failures.append((response.status, response.request.method, "/" + path))

    def check(self, *allowed: tuple[int, str, str]) -> None:
        assert self.errors == []
        assert [f for f in self.failures if f not in allowed] == []


# --- the API ---------------------------------------------------------------------------------


class Api:
    """``AppApi`` calls with a token that fail loudly."""

    def __init__(self, app_api: AppApi, token: str) -> None:
        self.app_api = app_api
        self.token = token

    def call(self, method: str, path: str, body: Any = None, *, ok: int = 300) -> Any:
        r = self.app_api.request(method, path, token=self.token, json=body)
        assert r.status_code < ok, (method, path, r.status_code, r.text)
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text

    def connections(self) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for c in self.call("GET", "/api/connections")["connections"]}

    def ensure(self, name: str, **fields: Any) -> dict[str, Any]:
        """The connection called ``name``, created with ``fields`` if missing."""
        have = self.connections().get(name)
        if have is not None:
            return have
        body = {"name": name, "port": 22, "auth_method": "key", **fields}
        created: dict[str, Any] = self.call("POST", "/api/connections", body)
        return created

    def delete_connection(self, name: str) -> None:
        conn = self.connections().get(name)
        if conn is not None:
            self.call("DELETE", f"/api/connections/{conn['id']}")

    def key(self, name: str) -> dict[str, Any]:
        keys: list[dict[str, Any]] = self.call("GET", "/api/keys")["keys"]
        return next(k for k in keys if k["name"] == name)

    def kill_sessions(self) -> None:
        """Close every session, so a reload does not bring back an earlier test's tabs."""
        for s in self.call("GET", "/api/sessions")["sessions"]:
            self.app_api.request("DELETE", f"/api/sessions/{s['session_id']}", token=self.token)

    def forget_host_keys(self, host: str, port: int = 22) -> None:
        for k in self.call("GET", "/api/hostkeys")["host_keys"]:
            if k["host"] == host and k["port"] == port:
                self.call("DELETE", f"/api/hostkeys/{k['id']}", {"confirm_host": host})


def reset_ssh_user(
    user: str, *, totp: tb.Totp | None = None, server: tb.SshServer = tb.SSHD
) -> None:
    """Put an sshd user's ~/.ssh back as the image had it (after a wizard installed a key)."""
    from tests.fixtures.sshclient import reset_user

    # Playwright's sync API keeps an event loop running in this thread, so use another one.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, reset_user(user, totp=totp, server=server)).result(timeout=60)


# --- the page ----------------------------------------------------------------------------------


def open_app(page: Page, base_url: str, token: str) -> Watch:
    """Install the problem watchers, store ``token`` and show the app (signed in)."""
    page.context.add_init_script(script=CSP_WATCH)
    page.context.add_init_script(
        script=f"try {{ localStorage.setItem('portkey.token', {token!r}); }} catch (e) {{}}"
    )
    watch = Watch(page)
    page.goto(base_url + "/")
    page.wait_for_selector(".app .tabstrip [role=tab]")
    return watch


def sidebar_row(page: Page, name: str) -> Locator:
    return page.locator(".tree .tree-conn").filter(
        has=page.locator(f".conn-row__name:text-is('{name}')")
    )


def open_row(page: Page, name: str) -> None:
    """Click a connection in the sidebar (connects, or focuses its open session)."""
    sidebar_row(page, name).first.locator(".conn-row__name").click()


def row_menu(page: Page, name: str, item: str) -> None:
    """Right-click a sidebar connection and choose ``item`` (its "⋯" shows only on hover)."""
    sidebar_row(page, name).first.click(button="right")
    page.get_by_role("menu", name=name).get_by_role("menuitem", name=item).click()


def active_panel(page: Page) -> Locator:
    return page.locator(".tabpanel:not([hidden])")


def active_tab(page: Page) -> Locator:
    return page.locator(".tabstrip [role=tab][aria-selected=true]")


def tab_named(page: Page, title: str) -> Locator:
    return page.locator(".tabstrip [role=tab]").filter(
        has=page.locator(f".tab__title:text-is('{title}')")
    )


def connected(page: Page, timeout: float = 20_000) -> None:
    expect(active_tab(page).locator(".tab__dot--connected")).to_have_count(1, timeout=timeout)


def rows(page: Page) -> Locator:
    return active_panel(page).locator(".xterm-rows")


def type_command(page: Page, command: str, marker: str, timeout: float = 10_000) -> None:
    """Type ``command`` into the active terminal and wait until ``marker`` is on screen.

    Use a marker the shell computes (``echo x-$((6*7))`` shows ``x-42``), never text that is
    in the command line itself, which the echo of the typing would match."""
    active_panel(page).locator(".xterm-helper-textarea").focus()
    page.keyboard.type(command + "\r")
    expect(rows(page)).to_contain_text(marker, timeout=timeout)


def toast(page: Page, text: str | re.Pattern[str]) -> None:
    expect(page.locator("#toasts")).to_contain_text(text)
