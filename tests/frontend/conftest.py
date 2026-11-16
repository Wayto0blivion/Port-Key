"""Fixtures for the frontend shell tests (package 8a).

These run in the private Playwright image built from tests/frontend/Dockerfile (see run.sh):
Chromium loads ``portkey/static`` from an in-process static server with the production CSP,
and ``mock/server.js`` stands in for the API. Nothing listens outside the container.

- ``app``: factory ``app(scenario="normal", signed_in=True, width=1440, height=900)`` -> Page.
  The page records console errors, page errors and CSP violations in ``page.problems``;
  the fixture fails the test if any were recorded (pass ``allow_errors=True`` to skip).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.frontend.serve import start

HERE = Path(__file__).parent
MOCK_DATA = HERE / "mock" / "data.js"
MOCK_SERVER = HERE / "mock" / "server.js"
TOKEN = "mock-token"
# Resolves once the store has finished its first load of every area (views render in the
# same microtask as the store update, so they are drawn by then).
LOADED = """async () => {
  const s = await import('/js/store.js');
  const done = () => Object.values(s.getState().status)
    .every((v) => v === 'ready' || v === 'error');
  while (!done()) await new Promise((r) => setTimeout(r, 20));
}"""


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    server, url = start()
    yield url
    server.shutdown()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


AppFactory = Callable[..., Page]


@pytest.fixture
def app(browser: Browser, base_url: str) -> Iterator[AppFactory]:
    pages: list[tuple[Page, bool]] = []

    def open_app(
        scenario: str = "normal",
        *,
        signed_in: bool = True,
        width: int = 1440,
        height: int = 900,
        allow_errors: bool = False,
        wait: bool = True,
    ) -> Page:
        context = browser.new_context(viewport={"width": width, "height": height})
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_init_script(script=f"window.__PK_MOCK_SCENARIO = {json.dumps(scenario)};")
        context.add_init_script(path=str(MOCK_DATA))
        context.add_init_script(path=str(MOCK_SERVER))
        if signed_in:
            context.add_init_script(
                script=f"try {{ localStorage.setItem('portkey.token', '{TOKEN}'); }} catch (e) {{}}"
            )
        page = context.new_page()
        problems: list[str] = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.problems = problems  # type: ignore[attr-defined]
        page.goto(f"{base_url}/index.html")
        if wait and signed_in:
            page.wait_for_selector(".app .tabstrip [role=tab]")
            page.evaluate(LOADED)
        pages.append((page, allow_errors))
        return page

    yield open_app
    for page, allow in pages:
        problems: list[str] = page.problems  # type: ignore[attr-defined]
        page.context.close()
        if not allow:
            assert problems == [], f"console errors: {problems}"


def calls(page: Page) -> list[dict[str, Any]]:
    """Requests the mock API received, oldest first."""
    result: list[dict[str, Any]] = page.evaluate("window.__pkMock.calls")
    return result


def toast_text(page: Page) -> str:
    text: str = page.locator("#toasts .toast").last.inner_text()
    return text


def expect_toast(page: Page, text: str) -> None:
    """Wait for a toast whose message starts with `text`."""
    expect(page.locator("#toasts .toast__text").filter(has_text=text).last).to_be_visible()
