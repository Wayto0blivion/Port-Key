"""Drive package 8b's frontend against the real app and the sshd test bed (test-only tool).

Not part of the pytest suite: it needs the e2e stack (package 7). Run it in the e2e image on the
compose network, nothing published:
    docker compose -f compose.dev.yaml -p portkey-wp8b --profile e2e up -d --wait app
    PORTKEY_APP_LOG="$(docker compose -f compose.dev.yaml -p portkey-wp8b --profile e2e \
        logs --no-color app)" \
    docker compose -f compose.dev.yaml -p portkey-wp8b --profile e2e run --rm --no-deps e2e \
        python -m tests.frontend.terminal.smoke
    docker compose -f compose.dev.yaml -p portkey-wp8b --profile e2e down -v
(The app's log carries the first-visit setup code; tests/e2e/harness/app_log.py.)
It signs in, opens terminals (key, password, first-seen host key, through a jump host), types,
reloads and checks the replay, disconnects and reconnects, and installs a key with the wizard.
Prints one line per step and exits non-zero on the first failure. Rerunnable on a running stack.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.conftest import browser_args
from tests.e2e.harness.app_api import AppApi
from tests.fixtures import testbed as tb

BASE = os.environ.get("PORTKEY_BASE_URL", "http://localhost:8822")
COOP_NOISE = "Cross-Origin-Opener-Policy header has been ignored"
STAMP = str(int(time.time()))[-6:]


def ensure(api: AppApi, token: str, name: str, **fields: Any) -> dict[str, Any]:
    existing = api.request("GET", "/api/connections", token=token).json()["connections"]
    for c in existing:
        if c["name"] == name:
            return dict(c)
    body = {"name": name, "port": 22, "auth_method": "key", **fields}
    r = api.request("POST", "/api/connections", token=token, json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def panel(page: Page) -> Any:
    return page.locator(".tabpanel:not([hidden])")


def connected(page: Page) -> None:
    expect(
        page.locator(".tabstrip [role=tab][aria-selected=true] .tab__dot--connected")
    ).to_have_count(1, timeout=20000)


def open_row(page: Page, name: str) -> None:
    page.locator(".sidebar").get_by_text(name, exact=True).first.click()


def shell(page: Page, command: str, marker: str) -> None:
    panel(page).locator(".xterm-helper-textarea").focus()
    page.keyboard.type(f"{command}\r")
    expect(panel(page).locator(".xterm-rows")).to_contain_text(marker, timeout=10000)


def main() -> int:
    api = AppApi(BASE)
    api.wait_healthy()
    token = api.ensure_password()
    # A rerun would otherwise get the last run's detached sessions back as tabs.
    for s in api.request("GET", "/api/sessions", token=token).json()["sessions"]:
        api.request("DELETE", f"/api/sessions/{s['session_id']}", token=token)
    keyonly = ensure(api, token, "smoke-keyonly", host=tb.SSHD.host, username="keyonly")
    ensure(api, token, "smoke-pw", host=tb.SSHD.host, username="pw", auth_method="password")
    ensure(api, token, "smoke-rekeyed", host=tb.SSHD_REKEYED.host, username="keyonly")
    ensure(
        api,
        token,
        "smoke-inner",
        host=tb.SSHD_INNER.host,
        username="keyonly",
        jump_id=keyonly["id"],
    )
    keys = api.request("GET", "/api/keys", token=token).json()["keys"]
    locked = next(k for k in keys if k["name"] == "id_ed25519_pass")
    ensure(api, token, "smoke-locked", host=tb.SSHD.host, username="keyonly", key_fp=locked["fp"])
    problems: list[str] = []
    steps: list[tuple[str, Callable[[Page], None]]] = []

    def step(name: str) -> Callable[[Callable[[Page], None]], Callable[[Page], None]]:
        def wrap(fn: Callable[[Page], None]) -> Callable[[Page], None]:
            steps.append((name, fn))
            return fn

        return wrap

    @step("key login, type, output")
    def _key(page: Page) -> None:
        open_row(page, "smoke-keyonly")
        connected(page)
        shell(page, "echo x-$((6*7))", "x-42")

    @step("reload restores the tab with a full replay")
    def _reload(page: Page) -> None:
        page.reload()
        page.wait_for_selector(".app .tabstrip [role=tab]")
        connected(page)
        expect(panel(page).locator(".xterm-rows")).to_contain_text("x-42")
        shell(page, "echo y-$((7*7))", "y-49")

    @step("password prompt")
    def _pw(page: Page) -> None:
        open_row(page, "smoke-pw")
        dialog = page.get_by_role("dialog", name="Sign in to smoke-pw")
        dialog.locator("input").fill(tb.USERS["pw"].password)
        page.keyboard.press("Enter")
        connected(page)
        shell(page, "echo p-$((2*21))", "p-42")

    @step("first-seen host key")
    def _rekeyed(page: Page) -> None:
        open_row(page, "smoke-rekeyed")
        trust = page.get_by_role("dialog", name="Check this host's identity")
        live = page.locator(".tabstrip [role=tab][aria-selected=true] .tab__dot--connected")
        expect(trust.or_(live)).to_be_visible(timeout=15000)
        if trust.count():  # trusted already on an earlier run otherwise
            trust.get_by_role("button", name="Trust this host").click()
        connected(page)

    @step("through a jump host")
    def _inner(page: Page) -> None:
        open_row(page, "smoke-inner")
        connected(page)
        expect(panel(page).locator(".sbar__via")).to_have_text("via smoke-keyonly")
        shell(page, "echo j-$((3*14))", "j-42")

    @step("disconnect, then Enter reconnects")
    def _disconnect(page: Page) -> None:
        panel(page).get_by_role("button", name="Disconnect").click()
        expect(panel(page).locator(".session__bottom")).to_contain_text(
            "Disconnected from smoke-inner"
        )
        panel(page).locator(".xterm-helper-textarea").focus()
        page.keyboard.press("Enter")
        connected(page)

    @step("passphrase prompt for an encrypted key")
    def _locked(page: Page) -> None:
        api.request("POST", "/api/keys/lock", token=token)
        open_row(page, "smoke-locked")
        dialog = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
        dialog.get_by_label("Passphrase").fill("not it")
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
        expect(dialog.get_by_role("alert")).to_contain_text("doesn't unlock", timeout=15000)
        dialog.get_by_label("Passphrase").fill(tb.KEY_PASSPHRASE)
        page.keyboard.press("Enter")
        connected(page)
        # The app image is built from the tree at build time: rebuild it after changing the UI.
        expect(page.locator(".unlocked")).to_contain_text("1 key unlocked", timeout=10000)

    @step("wizard with keyboard-interactive 2FA")
    def _tfa(page: Page) -> None:
        page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
        wizard = page.get_by_role("dialog", name="Add connection")
        wizard.locator("#wz-host").fill(tb.SSHD.host)
        wizard.locator("#wz-user").fill("tfa")
        wizard.locator("#wz-name").fill(f"smoke-tfa-{STAMP}")
        wizard.get_by_role("button", name="Next").click()
        first = wizard.get_by_label("Password:")
        already = wizard.get_by_role("button", name="Save connection")
        expect(first.or_(already)).to_be_visible(timeout=20000)
        if already.count():
            print("      (tfa: key already installed by an earlier run)")
            already.click()
        else:
            first.fill(tb.USERS["tfa"].password)
            page.keyboard.press("Enter")
            code = wizard.get_by_label("Verification code:")
            expect(code).to_be_enabled(timeout=15000)
            code.fill(tb.Totp().fresh())
            page.keyboard.press("Enter")
        expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30000)
        wizard.get_by_role("button", name="Add another").click()
        wizard.get_by_role("button", name="Cancel").click()

    @step("wizard installs a key with the password")
    def _wizard(page: Page) -> None:
        page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
        wizard = page.get_by_role("dialog", name="Add connection")
        wizard.locator("#wz-host").fill(tb.SSHD.host)
        wizard.locator("#wz-user").fill("fresh")
        wizard.locator("#wz-name").fill(f"smoke-fresh-{STAMP}")
        wizard.get_by_role("button", name="Next").click()
        field = wizard.locator("#wz-password:not([disabled])")
        already = wizard.get_by_role("button", name="Save connection")
        expect(field.or_(already)).to_be_visible(timeout=20000)
        if already.count():
            print("      (fresh: key already installed by an earlier run)")
            already.click()
        else:
            field.fill(tb.USERS["fresh"].password)
            page.keyboard.press("Enter")
        expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30000)
        wizard.get_by_role("button", name="Open terminal").click()
        connected(page)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=browser_args(BASE))
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(script=f"localStorage.setItem('portkey.token', {token!r});")
        page = context.new_page()
        page.on(
            "console",
            lambda m: (
                problems.append(m.text) if m.type == "error" and COOP_NOISE not in m.text else None
            ),
        )
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{BASE}/index.html")
        page.wait_for_selector(".app .tabstrip [role=tab]")
        failed = 0
        for name, fn in steps:
            try:
                fn(page)
                print(f"ok    {name}")
            except Exception as exc:
                failed = 1
                print(f"FAIL  {name}: {str(exc).splitlines()[0]}")
                page.screenshot(path="/tmp/smoke-failure.png")
                break
        browser.close()
    for p in problems:
        print(f"console: {p}")
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main())
