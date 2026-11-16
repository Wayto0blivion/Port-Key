"""Helpers for the frontend terminal tests (package 8b): reading the rendered terminal rows,
the messages the client sent on its sockets, and driving the scripted WebSocket mock."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Locator, Page, expect

TermFactory = Callable[..., Page]


def run(page: Page, module: str, body: str) -> Any:
    """Evaluate `body` (an expression using `m`, the imported module) in the page."""
    return page.evaluate(
        f"async () => {{ const m = await import('/js/{module}'); return {body}; }}"
    )


def panel(page: Page) -> Locator:
    """The visible tab panel."""
    return page.locator(".tabpanel:not([hidden])")


def rows(page: Page) -> Locator:
    """The rendered rows of the visible terminal (xterm's DOM renderer)."""
    return panel(page).locator(".xterm-rows")


def expect_rows(page: Page, text: str) -> None:
    expect(rows(page)).to_contain_text(text)


def lines(page: Page) -> list[str]:
    """The visible terminal's rows, one string per row, trailing spaces removed."""
    result: list[str] = page.evaluate(
        """() => [...document.querySelectorAll('.tabpanel:not([hidden]) .xterm-rows > div')]
          .map((r) => r.textContent.replace(/\\s+$/, ''))"""
    )
    return result


def connect(page: Page, name: str) -> None:
    """Open a session from the sidebar row."""
    page.locator(".sidebar").get_by_text(name, exact=True).first.click()


def connected(page: Page, name: str) -> None:
    """Open `name` and wait for its shell prompt."""
    connect(page, name)
    wait_connected(page)
    expect_rows(page, "$ ")


def type_in_terminal(page: Page, text: str) -> None:
    panel(page).locator(".xterm-helper-textarea").focus()
    page.keyboard.type(text)


def sent(page: Page, index: int = -1, path: str = "/ws/terminal") -> list[dict[str, Any]]:
    """Messages the client sent on the index-th socket to `path`."""
    result: list[dict[str, Any]] = page.evaluate(
        "([i, p]) => { const s = __pkWs.sockets.filter((x) => x.path === p);"
        " return s[i < 0 ? s.length + i : i].sent; }",
        [index, path],
    )
    return result


def sockets(page: Page, path: str = "/ws/terminal") -> int:
    count: int = page.evaluate("(p) => __pkWs.sockets.filter((x) => x.path === p).length", path)
    return count


def session_ids(page: Page) -> list[str]:
    result: list[str] = page.evaluate("__pkWs.live()")
    return result


def received(page: Page) -> int:
    """Bytes the visible session has counted (attach.since)."""
    count: int = page.evaluate(
        """async () => {
          const panel = document.querySelector('.tabpanel:not([hidden])');
          const m = await import('/js/views/sessions.js');
          return m.viewFor(panel.dataset.panel).received;
        }"""
    )
    return count


def wait_until(page: Page, js: str, arg: Any = None, timeout: float = 5000) -> Any:
    """Poll `js` (a function source) until it returns something truthy. Playwright's
    wait_for_function evaluates a string, which the app's CSP forbids."""
    waited = 0.0
    while True:
        value = page.evaluate(js, arg)
        if value:
            return value
        if waited >= timeout:
            raise AssertionError(f"timed out waiting for {js}")
        page.wait_for_timeout(50)
        waited += 50


def wait_connected(page: Page) -> None:
    """The visible session is connected (its status text is gone and the dot is green)."""
    expect(
        page.locator(".tabstrip [role=tab][aria-selected=true] .tab__dot--connected")
    ).to_have_count(1)


def fast_ping(page: Page, ms: int = 200) -> None:
    run(page, "ws/protocol.js", f"m.setTimings({{ping: {ms}}})")


def tab(page: Page, title: str) -> Locator:
    return page.locator(".tabstrip [role=tab]").filter(has_text=title)
