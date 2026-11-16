"""The quick-connect palette (package 8b): ranking and parsing (pure functions run in the
page), the open-session hint, Enter / Shift+Enter / Esc, #tag narrowing, commands and the
"Add <text> as a new connection…" row."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect

from tests.frontend.terminal.helpers import TermFactory, connected, run, tab, wait_connected

CONNS = """[
  {id: 'a', name: 'web-prod', host: '192.168.10.42', port: 22, username: 'john',
   notes: 'Production', group_id: 'g', tags: ['linux', 'prod'], jump_id: null,
   last_used_at: null, use_count: 0},
  {id: 'b', name: 'imaging-01', host: '192.168.10.31', port: 2222, username: 'admin', notes: '',
   group_id: null, tags: ['appliance'], jump_id: null, last_used_at: null, use_count: 0},
  {id: 'c', name: 'build-box', host: '192.168.10.20', port: 22, username: 'john',
   notes: 'CI builds', group_id: null, tags: ['linux'], jump_id: null,
   last_used_at: new Date().toISOString(), use_count: 30},
  {id: 'd', name: 'webby', host: '10.0.0.9', port: 22, username: 'root', notes: '',
   group_id: null, tags: [], jump_id: null, last_used_at: null, use_count: 0},
]"""
PATHS = "(g) => g === 'g' ? 'Office LAN / Servers' : ''"


def rank(page: Page, query: str) -> list[str]:
    body = f"m.rankConnections({CONNS}, {query!r}, {PATHS}).map((c) => c.id)"
    result: list[str] = run(page, "ui/palette.js", body)
    return result


def test_rank_connections(term: TermFactory) -> None:
    page = term()
    assert rank(page, "web")[:2] == ["a", "d"]  # prefix of the name first
    assert rank(page, "web-prod") == ["a"]
    assert rank(page, "192.168.10") == ["c", "b", "a"]  # recent use first, then by name
    assert rank(page, "admin") == ["b"]
    assert rank(page, "john@192.168.10.42") == ["a"]
    assert rank(page, "office") == ["a"]  # folder path
    assert rank(page, "#linux") == ["c", "a"]
    assert rank(page, "#linux prod") == ["a"]
    assert rank(page, "#lin") == ["c", "a"]  # a tag prefix narrows while typing
    assert rank(page, "ci builds") == ["c"]  # notes
    assert rank(page, "wbprd") == ["a"]  # fuzzy: letters in order
    assert rank(page, "nothing-like-this") == []


def test_parse_target(term: TermFactory) -> None:
    page = term()

    def parse(text: str) -> dict[str, Any]:
        result: dict[str, Any] = run(page, "ui/palette.js", f"m.parseTarget({text!r})")
        return result

    assert parse("admin@192.168.10.31:2222") == {
        "username": "admin",
        "host": "192.168.10.31",
        "port": 2222,
    }
    assert parse("192.168.10.42") == {"username": None, "host": "192.168.10.42", "port": None}
    assert parse(" nas-02:22 ") == {"username": None, "host": "nas-02", "port": 22}
    assert parse("ssh -p 2200 root@lab-01") == {"username": "root", "host": "lab-01", "port": 2200}
    assert parse("ssh -l admin -i key lab-01") == {
        "username": "admin",
        "host": "lab-01",
        "port": None,
    }
    assert parse("[fd00::5]:2222") == {"username": None, "host": "fd00::5", "port": 2222}
    assert parse("fd00::5") == {"username": None, "host": "fd00::5", "port": None}
    assert parse("ssh://john@build-box:22/") == {
        "username": "john",
        "host": "build-box",
        "port": 22,
    }
    assert parse("host:99999") == {"username": None, "host": "host", "port": None}


def open_palette(page: Page) -> Any:
    page.get_by_role("button", name="Connect to…").click()
    dialog = page.get_by_role("dialog", name="Quick connect")
    expect(dialog.get_by_role("combobox", name="Connect to")).to_be_focused()
    return dialog


def test_enter_connects_and_focuses_an_open_session(term: TermFactory) -> None:
    page = term()
    dialog = open_palette(page)
    expect(dialog.locator(".palette__row--conn").first).to_contain_text(
        "web-prod"
    )  # most recent first
    expect(dialog.get_by_role("option").last).to_have_text("Add connection…")
    dialog.get_by_role("combobox").fill("web")
    expect(dialog.get_by_role("option").first).to_have_attribute("aria-selected", "true")
    expect(dialog.get_by_role("option").last).to_contain_text("Add web as a new connection…")
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    wait_connected(page)
    page.get_by_role("tab", name="Home").click()
    dialog = open_palette(page)
    dialog.get_by_role("combobox").fill("web-prod")
    expect(dialog.get_by_role("option").first).to_contain_text("1 session open · Enter to focus")
    page.keyboard.press("Enter")
    expect(tab(page, "web-prod")).to_have_attribute(
        "aria-selected", "true"
    )  # focused, not a new one
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(2)
    dialog = open_palette(page)
    dialog.get_by_role("combobox").fill("web-prod")
    page.keyboard.press("Shift+Enter")
    expect(tab(page, "web-prod · 2")).to_be_visible()


def test_arrows_tags_and_escape(term: TermFactory) -> None:
    page = term()
    connected(page, "build-box")
    page.locator(".tabpanel:not([hidden]) .xterm-helper-textarea").focus()
    page.keyboard.press("Control+Shift+K")
    dialog = page.get_by_role("dialog", name="Quick connect")
    box = dialog.get_by_role("combobox")
    box.fill("#appliance")
    rows = dialog.locator(".palette__row--conn")
    expect(rows).to_have_count(2)
    expect(rows).to_contain_text(["imaging-01", "parser-vm"])
    page.keyboard.press("ArrowDown")
    expect(dialog.get_by_role("option").nth(1)).to_have_attribute("aria-selected", "true")
    expect(box).to_have_attribute("aria-activedescendant", "palette-opt-1")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.locator(".tabpanel:not([hidden]) .xterm-helper-textarea")).to_be_focused()


def test_commands(term: TermFactory) -> None:
    page = term()
    dialog = open_palette(page)
    expect(dialog.locator(".palette__row--cmd")).to_have_text(
        ["Generate keyCommand", "KeysCommand", "SettingsCommand", "Lock keysCommand"]
    )
    dialog.get_by_role("combobox").fill("keys")
    expect(dialog.locator(".palette__row--cmd")).to_have_count(2)  # Keys, Lock keys
    dialog.locator(".palette__row--cmd", has_text="Keys").first.click()
    expect(page.get_by_role("tab", name="Keys")).to_have_attribute("aria-selected", "true")


def test_add_row_opens_the_wizard_prefilled(term: TermFactory) -> None:
    page = term()
    dialog = open_palette(page)
    dialog.get_by_role("combobox").fill("admin@lab-01:2200")
    dialog.get_by_role("option").last.click()
    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("lab-01")
    expect(wizard.get_by_label("Port", exact=True)).to_have_value("2200")
    expect(wizard.get_by_label("User", exact=True)).to_have_value("admin")
    expect(wizard.get_by_label("Name", exact=True)).to_have_value("lab-01")


def test_close_all_sessions(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    connected(page, "build-box")
    dialog = open_palette(page)
    dialog.get_by_role("combobox").fill("close all")
    page.keyboard.press("Enter")
    confirm = page.get_by_role("dialog", name="Close all 2 sessions?")
    expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
    confirm.get_by_role("button", name="Close 2 sessions").click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)
