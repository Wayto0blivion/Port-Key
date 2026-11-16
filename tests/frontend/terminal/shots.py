"""Screenshots of package 8b's screens against the mocks (test-only tool).

Run in the frontend image with an output directory mounted, for example:
    docker compose -f compose.dev.yaml -p portkey-wp8b --profile frontend run --rm --no-deps \
        -v /abs/out:/out frontend python -m tests.frontend.terminal.shots /out [names]
Writes <name>-<width>.png at 1440 and 800 wide, for comparing with docs/design/Terminal,
Disconnected, AuthFailed, HostKeyChanged, Passphrase, Palette and Wizard-*.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tests.frontend.conftest import LOADED, MOCK_DATA, MOCK_SERVER, TOKEN
from tests.frontend.screenshots import FONT_FIX
from tests.frontend.serve import start
from tests.frontend.terminal.conftest import MOCK_INSTALL, MOCK_WS

WIDTHS = (1440, 800)


def _connect(page: Page, name: str) -> None:
    if page.locator(".topbar__menu").is_visible():
        page.click(".topbar__menu")
        page.wait_for_timeout(250)
    page.locator(".sidebar").get_by_text(name, exact=True).first.click()


def terminal(page: Page) -> None:
    _connect(page, "web-prod")
    page.wait_for_selector(".tab__dot--connected")
    page.locator(".tabpanel:not([hidden]) .xterm-helper-textarea").focus()
    page.keyboard.type("docker compose ps\r")
    page.wait_for_timeout(200)
    page.keyboard.type("uptime\r")


def connecting(page: Page) -> None:
    page.evaluate("__pkWs.behave('slow', 'yes'); __pkWs.behave('web-prod', 'slow')")
    _connect(page, "web-prod")
    page.wait_for_timeout(700)


def disconnected(page: Page) -> None:
    _connect(page, "imaging-01")
    page.get_by_label("Passphrase").fill("open sesame")
    page.keyboard.press("Enter")
    page.wait_for_selector(".tab__dot--connected")
    page.evaluate("() => __pkWs.exit(__pkWs.live()[0], 'lost')")


def auth_failed(page: Page) -> None:
    _connect(page, "backup-nas")
    page.wait_for_selector(".session__panel:not([hidden])")


def hostkey_changed(page: Page) -> None:
    page.evaluate("__pkWs.behave('web-prod', 'hostkey_changed')")
    _connect(page, "web-prod")
    page.wait_for_selector("dialog[open]")


def first_seen(page: Page) -> None:
    _connect(page, "parser-vm")
    page.wait_for_selector("dialog[open]")


def passphrase(page: Page) -> None:
    _connect(page, "imaging-01")
    page.wait_for_selector("dialog[open]")


def palette(page: Page) -> None:
    page.click(".quick")
    page.fill(".palette__input", "192.168.10")


def _open_wizard(page: Page) -> None:
    # Not awaited: openWizard resolves only when the dialog closes.
    page.evaluate("() => { import('/js/views/wizard.js').then((m) => { m.openWizard({}); }); }")
    page.wait_for_selector("#wz-host")


def wizard_host(page: Page) -> None:
    _open_wizard(page)
    page.locator("#wz-host").fill("192.168.10.42")
    page.locator("#wz-name").fill("web-lab")
    page.locator("#wz-tags").fill("linux,docker,")
    page.get_by_label("Red").check()


def _wizard_run(page: Page, host: str) -> None:
    _open_wizard(page)
    page.locator("#wz-host").fill(host)
    page.get_by_role("button", name="Next").click()


def wizard_identity(page: Page) -> None:
    _wizard_run(page, "new-host")
    page.wait_for_selector("text=Trust this host")


def wizard_signin(page: Page) -> None:
    _wizard_run(page, "lab-01")
    page.wait_for_selector("#wz-password:not([disabled])")


def wizard_saved(page: Page) -> None:
    wizard_signin(page)
    page.locator("#wz-password").fill("pa55word")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Connection saved")


def wizard_nopass(page: Page) -> None:
    _wizard_run(page, "nopass-host")
    page.wait_for_selector("text=Check key login")


def wizard_timeout(page: Page) -> None:
    _wizard_run(page, "10.9.9.9")
    page.wait_for_selector("text=Edit host")


SHOTS: list[tuple[str, Callable[[Page], None]]] = [
    ("terminal", terminal),
    ("connecting", connecting),
    ("disconnected", disconnected),
    ("authfailed", auth_failed),
    ("hostkeychanged", hostkey_changed),
    ("firstseen", first_seen),
    ("passphrase", passphrase),
    ("palette", palette),
    ("wizard-host", wizard_host),
    ("wizard-identity", wizard_identity),
    ("wizard-signin", wizard_signin),
    ("wizard-saved", wizard_saved),
    ("wizard-nopass", wizard_nopass),
    ("wizard-timeout", wizard_timeout),
]


def main(out: Path, only: set[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    server, url = start()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, action in SHOTS:
            if only and name not in only:
                continue
            for width in WIDTHS:
                # bypass_csp only so the font override applies (see tests/frontend/screenshots.py).
                ctx = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)
                ctx.add_init_script(script="window.__PK_MOCK_SCENARIO = 'normal';")
                for path in (MOCK_DATA, MOCK_SERVER, MOCK_WS, MOCK_INSTALL):
                    ctx.add_init_script(path=str(path))
                ctx.add_init_script(script=f"localStorage.setItem('portkey.token', '{TOKEN}');")
                page = ctx.new_page()
                page.goto(f"{url}/index.html")
                page.add_style_tag(content=FONT_FIX)
                page.wait_for_selector(".app .tabstrip [role=tab]")
                page.evaluate(LOADED)
                action(page)
                page.wait_for_timeout(400)
                page.screenshot(path=str(out / f"{name}-{width}.png"))
                ctx.close()
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1]), set(sys.argv[2:]))
