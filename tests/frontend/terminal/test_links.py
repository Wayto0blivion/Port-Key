"""Hyperlinks a server prints (OSC 8): never on a plain click; Ctrl+click asks in Port-Key's
own dialog first (security review: xterm's default opened them after a generic confirm())."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.frontend.terminal.helpers import TermFactory, connected, panel, run, session_ids

LINK = "\\x1b]8;;http://phish.example/login?next=portkey\\x1b\\\\"
END = "\\x1b]8;;\\x1b\\\\"


def print_link(page: Page, text: str) -> None:
    sid = session_ids(page)[0]
    page.evaluate(f"(id) => __pkWs.push(id, '\\r\\n{LINK}{text}{END}\\r\\n')", sid)
    expect(panel(page).locator(".xterm-rows")).to_contain_text(text)


def link_center(page: Page, text: str) -> tuple[float, float]:
    span = panel(page).locator(".xterm-rows span", has_text=text).last
    box = span.bounding_box()
    assert box is not None
    return box["x"] + 4, box["y"] + box["height"] / 2


def test_a_plain_click_on_a_server_link_does_nothing(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    print_link(page, "Re-authenticate-here")
    x, y = link_center(page, "Re-authenticate-here")
    page.mouse.move(x, y)
    page.mouse.click(x, y)
    page.wait_for_timeout(300)
    assert page.dialogs == []  # type: ignore[attr-defined]  # no browser confirm()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert len(page.context.pages) == 1


def test_ctrl_click_asks_and_names_the_real_host(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    print_link(page, "intranet-login")
    x, y = link_center(page, "intranet-login")
    page.mouse.move(x, y)
    page.keyboard.down("Control")
    page.mouse.click(x, y)
    page.keyboard.up("Control")
    dialog = page.get_by_role("dialog", name="Open a link from web-prod?")
    expect(dialog).to_contain_text("The server printed a link to phish.example.")
    expect(dialog).to_contain_text("http://phish.example/login?next=portkey")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    assert len(page.context.pages) == 1


def test_safe_url_accepts_http_and_https_only(term: TermFactory) -> None:
    page = term()
    got = run(
        page,
        "terminal/links.js",
        "['https://a.example/x', 'http://b.example', 'javascript:alert(1)', 'file:///etc/passwd',"
        " 'data:text/html,hi', 'not a url'].map((u) => m.safeUrl(u)?.href ?? null)",
    )
    assert got == ["https://a.example/x", "http://b.example/", None, None, None, None]
