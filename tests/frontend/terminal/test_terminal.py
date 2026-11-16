"""Terminal sessions: open, type, session bar, colour frame, tabs, font size, Find, Disconnect
and Reconnect (package 8b). The WebSocket server is the scripted mock in mock_ws.js."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.frontend.terminal.helpers import (
    TermFactory,
    connect,
    connected,
    expect_rows,
    lines,
    panel,
    rows,
    run,
    sent,
    session_ids,
    tab,
    type_in_terminal,
    wait_connected,
    wait_until,
)


def test_open_type_and_echo(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    msgs = sent(page)
    assert msgs[0] == {"type": "auth", "token": "mock-token"}  # auth is the first message
    assert msgs[1]["type"] == "open"
    assert msgs[1]["connection_id"]
    assert msgs[1]["cols"] > 20
    assert msgs[1]["rows"] > 5
    assert msgs[1]["force_password"] is False
    expect_rows(page, "Welcome to Ubuntu 24.04 LTS")
    expect(panel(page).locator(".xterm-helper-textarea")).to_be_focused()  # focus in the terminal
    type_in_terminal(page, "echo hi\r")
    expect_rows(page, "john@web-prod:~$ echo hi")
    expect(rows(page)).to_contain_text("echo hihi")
    shown = lines(page)
    i = shown.index("john@web-prod:~$ echo hi")
    assert shown[i + 1] == "hi"
    assert shown[i + 2] == "john@web-prod:~$"
    binary = [m for m in sent(page) if "binary" in m]
    assert "".join(m["binary"] for m in binary) == "echo hi\r"
    assert all(m["size"] <= 65536 for m in binary)


def test_session_bar_frame_and_tab(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    bar = panel(page).locator(".sbar")
    expect(bar.locator(".sbar__who")).to_have_text("john@web-prod")
    expect(bar.locator(".sbar__where")).to_have_text("192.168.10.42:22")
    expect(bar.locator(".sbar__key")).to_have_text("id_ed25519")
    expect(bar.locator(".sbar__uptime")).to_have_text("Connected just now")
    expect(bar.locator(".sbar__via")).to_be_hidden()
    frame = panel(page).locator(".session__frame")
    assert frame.evaluate("e => getComputedStyle(e).borderTopColor") == "rgb(248, 81, 73)"  # red
    t = tab(page, "web-prod")
    expect(t).to_have_class(re.compile(r"lbl-red"))
    expect(t.locator(".tab__dot")).to_have_class(re.compile("tab__dot--connected"))


def test_second_session_title_and_focus_existing(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    connect(page, "web-prod")  # a plain click focuses the open session
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(2)
    page.locator(".sidebar").get_by_text("web-prod", exact=True).first.click(modifiers=["Control"])
    expect(tab(page, "web-prod · 2")).to_be_visible()
    expect_rows(page, "$ ")
    assert len(session_ids(page)) == 2
    expect(page.locator(".sidebar .badge").first).to_contain_text("2")


def test_switching_tabs_focuses_the_shell_but_not_while_arrowing(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    connected(page, "build-box")
    page.keyboard.press("Alt+1")  # the first tab after Home
    expect(tab(page, "web-prod")).to_have_attribute("aria-selected", "true")
    expect(panel(page).locator(".xterm-helper-textarea")).to_be_focused()
    tab(page, "web-prod").focus()
    page.keyboard.press("ArrowRight")
    expect(tab(page, "build-box")).to_be_focused()  # roving focus stays in the tab strip
    page.wait_for_timeout(150)
    expect(tab(page, "build-box")).to_be_focused()
    tab(page, "web-prod").click()
    expect(panel(page).locator(".xterm-helper-textarea")).to_be_focused()
    page.locator(".tabstrip [role=tab]").first.click()  # Home: background tabs keep their shells
    page.evaluate("(id) => __pkWs.push(id, 'background output\\r\\n')", session_ids(page)[0])
    expect(tab(page, "web-prod").locator(".tab__dot")).to_have_class(
        re.compile("tab__dot--activity")
    )
    tab(page, "web-prod").click()
    expect(tab(page, "web-prod").locator(".tab__dot")).to_have_class(
        re.compile("tab__dot--connected")
    )
    expect_rows(page, "background output")


def test_via_jump_host_and_first_seen_key(term: TermFactory) -> None:
    page = term()
    connect(page, "parser-vm")
    dialog = page.get_by_role("dialog", name="Check this host's identity")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text(
        "This is the first time Port-Key has connected to parser-vm (192.168.10.27)."
    )
    expect(dialog).to_contain_text("Target: parser-vm")  # the hop label in a jump chain
    expect(dialog.locator(".fpbox__text")).to_contain_text(
        "ED25519 SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E"
    )
    expect(dialog.locator(".cmdbox")).to_have_text(
        "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
    )
    checklist = panel(page).locator(".checklist-live")
    expect(checklist).to_contain_text("Reaching build-box…")
    dialog.get_by_role("button", name="Trust this host").click()
    expect_rows(page, "root@parser-vm:~$ ")
    expect(panel(page).locator(".sbar__via")).to_have_text("via build-box")
    reply = [m for m in sent(page) if m.get("type") == "prompt_reply"]
    assert reply == [{"type": "prompt_reply", "id": "p1", "accept": True}]


def test_disconnect_then_enter_reconnects(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    panel(page).get_by_role("button", name="Disconnect").click()
    bar = panel(page).locator(".session__bottom")
    expect(bar).to_contain_text(re.compile(r"Disconnected from web-prod at \d\d:\d\d"))
    expect(bar).to_contain_text("Press Enter or select Reconnect to open a new shell.")
    expect(tab(page, "web-prod").locator(".tab__dot")).to_have_class(
        re.compile("tab__dot--disconnected")
    )
    expect(tab(page, "web-prod")).to_have_class(re.compile("tab--muted"))
    expect_rows(page, "Welcome to Ubuntu")  # the scrollback stays
    panel(page).locator(".xterm-helper-textarea").focus()
    page.keyboard.press("Enter")
    expect(bar).to_be_hidden()
    expect_rows(page, "--- Reconnecting to web-prod ---")
    wait_connected(page)
    assert len(session_ids(page)) == 1
    assert sent(page)[1]["type"] == "open"  # a new shell, not a reattach


def test_remote_exit_and_lost(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    type_in_terminal(page, "exit\r")
    bar = panel(page).locator(".session__bottom")
    expect(bar).to_contain_text("The remote host closed the connection.")
    bar.get_by_role("button", name="Reconnect").click()
    expect(bar).to_be_hidden()
    wait_connected(page)
    sid = session_ids(page)[0]
    page.evaluate("(id) => __pkWs.exit(id, 'lost')", sid)
    expect(bar).to_contain_text(re.compile(r"Connection to web-prod lost at \d\d:\d\d"))
    expect(bar).to_contain_text("No response for 90 seconds.")
    bar.get_by_role("button", name="Close tab").click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)


def test_font_size_buttons_change_every_terminal_and_save(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    expect(rows(page)).to_have_css("font-size", "14px")
    panel(page).get_by_role("button", name="Larger text").click()
    panel(page).get_by_role("button", name="Larger text").click()
    expect(rows(page)).to_have_css("font-size", "16px")
    patches = (
        "() => window.__pkMock.calls"
        ".filter((c) => c.path === '/api/settings' && c.method === 'PATCH' && c.body)"
        ".map((c) => c.body)"
    )
    wait_until(page, patches)
    page.wait_for_timeout(700)
    assert page.evaluate(patches) == [{"font_size": 16}]  # one save for both clicks
    panel(page).get_by_role("button", name="Smaller text").click()
    expect(rows(page)).to_have_css("font-size", "15px")


def test_find_in_scrollback(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    type_in_terminal(page, "seq 1 300\r")
    expect_rows(page, "300")
    page.keyboard.press("Control+Shift+F")
    find = panel(page).get_by_role("searchbox", name="Find in scrollback")
    expect(find).to_be_focused()
    find.fill("Welcome")
    selection = run(
        page,
        "views/sessions.js",
        "m.viewFor(document.querySelector('.tabpanel:not([hidden])').dataset.panel).term.getSelection()",
    )
    assert selection == "Welcome"
    find.fill("no such text")
    expect(panel(page).locator(".find__note")).to_have_text("No matches")
    page.keyboard.press("Escape")
    expect(find).to_be_hidden()
    expect(panel(page).locator(".xterm-helper-textarea")).to_be_focused()


def test_close_tab_kills_the_session(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    tab(page, "web-prod").get_by_role("button", name="Close web-prod").click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)
    wait_until(page, "() => __pkWs.live().length === 0")
    assert sent(page)[-1] == {"type": "close"}
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")


def test_a_deleted_connection_keeps_its_session(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    page.evaluate(
        """async () => {
          const api = await import('/js/api.js');
          const store = await import('/js/store.js');
          const c = store.getState().connections.find((x) => x.name === 'web-prod');
          await api.deleteConnection(c.id);
          await store.refreshConnections();
        }"""
    )
    expect(tab(page, "web-prod (deleted)")).to_be_visible()
    expect(panel(page).locator(".sbar__who")).to_have_text("john@web-prod (deleted)")
    type_in_terminal(page, "echo still here\r")
    expect_rows(page, "echo still herestill here")


def test_colours_render_under_the_production_csp(term: TermFactory) -> None:
    # The fixture fails on any CSP violation; xterm's DOM renderer injects <style> elements and
    # sets style attributes for 24-bit colour, which the app's CSP allows (style-src
    # 'unsafe-inline', portkey/security/headers.py).
    page = term()
    connected(page, "web-prod")
    type_in_terminal(page, "color\r")
    expect_rows(page, "red truecolor")
    red = rows(page).locator("span", has_text="red").first
    expect(red).to_have_css("color", "rgb(204, 0, 0)")  # ANSI red, from xterm's <style> element
    true = rows(page).locator("span", has_text="truecolor").first
    expect(true).to_have_css("color", "rgb(255, 128, 0)")


def test_copy_ssh_command(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    panel(page).get_by_role("button", name="Copy ssh command").click()
    expect(page.locator("#toasts")).to_contain_text("ssh command copied")
    assert page.evaluate("navigator.clipboard.readText()") == "ssh john@192.168.10.42"
