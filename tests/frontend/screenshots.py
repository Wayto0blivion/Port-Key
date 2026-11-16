"""Screenshots of every 8a view against the mock API (package 8a, test-only tool).

Run inside the frontend test image (tests/frontend/run.sh shots OUT_DIR). Writes
<view>-<width>.png for widths 1440 and 800 into OUT_DIR, for comparing with docs/design.
Package 9 can reuse it as a visual smoke check.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tests.frontend.conftest import MOCK_DATA, MOCK_SERVER, TOKEN
from tests.frontend.serve import start

WIDTHS = (1440, 800)
FONT_FIX = (
    ":root { --font-ui: 'Liberation Sans', sans-serif; --font-mono: 'Liberation Mono', monospace; }"
)


def _keys(page: Page) -> None:
    page.click(".topbar__btn:has-text('Keys')")
    page.wait_for_selector(".key-detail")


def _settings(page: Page) -> None:
    page.click(".topbar__btn:has-text('Settings')")
    page.wait_for_selector(".hostkeys table")


def _diagnostics(page: Page) -> None:
    page.click(".topbar__btn:has-text('Settings')")
    page.click("text=Open Diagnostics")
    page.wait_for_selector(".diag__report .about")


def _edit(page: Page) -> None:
    page.locator(".table--connections .name-btn", has_text="web-prod").locator(
        "xpath=../../.."
    ).click(button="right")
    page.click(".menu >> text=Edit")
    page.wait_for_selector("dialog.modal[open]")


def _generate(page: Page) -> None:
    _keys(page)
    page.click("button:has-text('Generate key') >> nth=0")
    page.wait_for_selector("dialog.modal[open]")


def _bulk(page: Page) -> None:
    page.check("[aria-label='Select parser-vm']")
    page.check("[aria-label='Select imaging-01']")
    page.wait_for_selector(".bulkbar")


def _drawer(page: Page) -> None:
    if page.locator(".topbar__menu").is_visible():
        page.click(".topbar__menu")
        page.wait_for_timeout(300)


def _filter(page: Page) -> None:
    _drawer(page)
    page.fill("#sidebar-filter", "#linux")


def _suggest(page: Page) -> None:
    _drawer(page)
    page.click(".sidebar__suggest-head")


SHOTS: list[tuple[str, str, bool, Callable[[Page], None] | None]] = [
    ("home", "normal", True, None),
    ("home-bulk", "normal", True, _bulk),
    ("sidebar-filter", "normal", True, _filter),
    ("sidebar-suggest", "normal", True, _suggest),
    ("edit", "normal", True, _edit),
    ("keys", "normal", True, _keys),
    ("generate", "normal", True, _generate),
    ("settings", "normal", True, _settings),
    ("diagnostics", "normal", True, _diagnostics),
    ("firstrun-key", "empty", True, None),
    ("firstrun-nokey", "nokey", True, None),
    ("firstrun-ppk", "ppk", True, None),
    ("many", "many", True, None),
    ("setup", "setup", False, None),
    ("signin", "signedout", False, None),
    ("loading", "slow", True, None),
    ("error", "error", True, None),
]


def main(out: Path, only: set[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    server, url = start()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, scenario, signed_in, action in SHOTS:
            if only and name not in only:
                continue
            for width in WIDTHS:
                # bypass_csp only so the font override below applies: the image's system-ui
                # font has no bold, which would misrepresent the Segoe UI look on Windows.
                ctx = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)
                ctx.add_init_script(script=f"window.__PK_MOCK_SCENARIO = '{scenario}';")
                ctx.add_init_script(path=str(MOCK_DATA))
                ctx.add_init_script(path=str(MOCK_SERVER))
                if signed_in:
                    ctx.add_init_script(script=f"localStorage.setItem('portkey.token', '{TOKEN}');")
                page = ctx.new_page()
                page.goto(f"{url}/index.html")
                page.add_style_tag(content=FONT_FIX)
                page.wait_for_timeout(250 if scenario == "slow" else 600)
                if action:
                    action(page)
                    page.wait_for_timeout(300)
                page.screenshot(path=str(out / f"{name}-{width}.png"))
                ctx.close()
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1]), set(sys.argv[2:]))
