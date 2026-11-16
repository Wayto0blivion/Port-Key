"""Fixtures for the frontend terminal tests (package 8b).

Same setup as ``tests/frontend/conftest.py`` (Chromium in the Playwright image, the static
files from ``tests/frontend/serve.py`` with the app's own Content-Security-Policy, the mock HTTP
API) plus:

- ``term``: factory ``term(scenario="normal", width=1440, height=900, allow_errors=False)`` ->
  Page, with the scripted WebSocket server (``mock_ws.js`` and ``mock_install.js``) injected
  before the page loads. Console errors, page errors and CSP violations fail the test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Dialog, Page

from tests.frontend.conftest import LOADED, MOCK_DATA, MOCK_SERVER, TOKEN
from tests.frontend.terminal.helpers import TermFactory

HERE = Path(__file__).parent
MOCK_WS = HERE / "mock_ws.js"
MOCK_INSTALL = HERE / "mock_install.js"
# Report CSP violations to the console as errors, so the fixture fails on them.
CSP_WATCH = """document.addEventListener('securitypolicyviolation', (e) => {
  console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`);
});"""


def new_page(browser: Browser, base_url: str, scenario: str, width: int, height: int) -> Page:
    context = browser.new_context(viewport={"width": width, "height": height})
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    context.add_init_script(script=f"window.__PK_MOCK_SCENARIO = {json.dumps(scenario)};")
    context.add_init_script(path=str(MOCK_DATA))
    context.add_init_script(path=str(MOCK_SERVER))
    context.add_init_script(path=str(MOCK_WS))
    context.add_init_script(path=str(MOCK_INSTALL))
    context.add_init_script(script=CSP_WATCH)
    context.add_init_script(
        script=f"try {{ localStorage.setItem('portkey.token', '{TOKEN}'); }} catch (e) {{}}"
    )
    page = context.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.problems = problems  # type: ignore[attr-defined]
    # Record dialogs the browser itself shows (the leave warning is a beforeunload dialog) and
    # let a reload go ahead.
    dialogs: list[str] = []
    page.dialogs = dialogs  # type: ignore[attr-defined]

    def on_dialog(d: Dialog) -> None:
        dialogs.append(d.type)
        if d.type == "beforeunload":
            d.accept()
        else:
            d.dismiss()

    page.on("dialog", on_dialog)
    page.goto(f"{base_url}/index.html")
    page.wait_for_selector(".app .tabstrip [role=tab]")
    page.evaluate(LOADED)
    return page


@pytest.fixture
def term(browser: Browser, base_url: str) -> Iterator[TermFactory]:
    pages: list[tuple[Page, bool]] = []

    def open_page(
        scenario: str = "normal",
        *,
        width: int = 1440,
        height: int = 900,
        allow_errors: bool = False,
    ) -> Page:
        page = new_page(browser, base_url, scenario, width, height)
        pages.append((page, allow_errors))
        return page

    yield open_page
    for page, allow in pages:
        problems: list[str] = page.problems  # type: ignore[attr-defined]
        page.context.close()
        if not allow:
            assert problems == [], f"console errors: {problems}"
