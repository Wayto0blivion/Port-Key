"""Sessions survive a dropped socket and a page reload (package 8b): attach with `since`,
the reset flag, dead-link detection by missed pongs, "attached elsewhere", and restoring tabs
after a reload with a full replay (ended sessions come back Disconnected)."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.frontend.terminal.helpers import (
    TermFactory,
    connected,
    expect_rows,
    fast_ping,
    lines,
    panel,
    received,
    sent,
    session_ids,
    sockets,
    tab,
    type_in_terminal,
    wait_connected,
    wait_until,
)


def test_drop_reattaches_with_since_and_no_gap(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    type_in_terminal(page, "echo before\r")
    expect_rows(page, "before")
    sid = session_ids(page)[0]
    count = received(page)
    total = page.evaluate("(id) => { const s = __pkWs.sessions.get(id); return s.base + s.buf.length; }", sid)
    assert count == total  # every byte the server sent is counted
    page.evaluate("(id) => __pkWs.drop(id)", sid)
    page.evaluate("(id) => __pkWs.push(id, '\\r\\nwritten while away\\r\\n')", sid)
    wait_connected(page)
    expect_rows(page, "written while away")
    attach = sent(page)[1]
    assert attach == {"type": "attach", "session_id": sid, "since": count, "cols": attach["cols"], "rows": attach["rows"]}
    assert sum(1 for line in lines(page) if line == "written while away") == 1  # appended once
    assert sum(1 for line in lines(page) if line.endswith("echo before")) == 1  # nothing replayed twice
    type_in_terminal(page, "echo after\r")  # input works again after the reattach
    wait_until(page, "() => document.querySelector('.tabpanel:not([hidden]) .xterm-rows').textContent.includes('echo afterafter')")


def test_reattach_with_reset_clears_and_replays(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    sid = session_ids(page)[0]
    page.evaluate("(id) => __pkWs.drop(id)", sid)
    # More output than the ring buffer holds went by while away: `since` is older than the
    # oldest byte the server still has, so it answers reset and replays what it kept.
    page.evaluate(
        "(id) => { const s = __pkWs.sessions.get(id); s.base += s.buf.length + 1000;"
        " s.buf = 'replayed tail\\r\\n'; }",
        sid,
    )
    wait_connected(page)
    expect_rows(page, "replayed tail")
    shown = "\n".join(lines(page))
    assert "Welcome to Ubuntu" not in shown  # cleared before the replay
    assert received(page) == page.evaluate(
        "(id) => { const s = __pkWs.sessions.get(id); return s.base + s.buf.length; }", sid
    )


def test_two_missed_pongs_count_as_a_dead_link(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    fast_ping(page, 150)
    # Reconnect so the new socket uses the short ping interval.
    sid = session_ids(page)[0]
    page.evaluate("(id) => __pkWs.drop(id)", sid)
    wait_connected(page)
    before = sockets(page)
    page.evaluate("() => __pkWs.mute(__pkWs.sockets.filter((s) => s.path === '/ws/terminal').at(-1))")
    wait_until(page, "(n) => __pkWs.sockets.filter((s) => s.path === '/ws/terminal').length > n", before, timeout=8000)
    wait_connected(page)
    pings = [m for m in sent(page, -2) if m.get("type") == "ping"]
    assert len(pings) == 2  # two unanswered pings, then the dead socket is dropped
    assert sent(page)[1]["type"] == "attach"
    assert session_ids(page) == [sid]


def test_attached_elsewhere_does_not_reattach_on_its_own(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    sid = session_ids(page)[0]
    count = sockets(page)
    page.evaluate("(id) => __pkWs.stealAttach(id)", sid)
    bar = panel(page).locator(".session__bottom")
    expect(bar).to_contain_text("web-prod is open in another tab")
    page.wait_for_timeout(600)
    assert sockets(page) == count
    bar.get_by_role("button", name="Reattach here").click()
    wait_connected(page)
    assert sent(page)[1]["type"] == "attach"


def test_reload_restores_tabs_with_a_full_replay(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    type_in_terminal(page, "echo kept across reload\r")
    expect_rows(page, "kept across reload")
    connected(page, "build-box")
    ended = session_ids(page)[1]
    tab(page, "web-prod").click()
    page.evaluate("(id) => __pkWs.exit(id, 'closed')", ended)  # this one ends before the reload
    expect(tab(page, "build-box")).to_have_class(re.compile("tab--muted"))
    page.reload()
    page.wait_for_selector(".app .tabstrip [role=tab]")
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(2)  # Home, web-prod
    expect(tab(page, "web-prod")).to_have_attribute("aria-selected", "true")
    wait_connected(page)
    expect_rows(page, "kept across reload")
    attaches = page.evaluate("__pkWs.attaches")
    assert attaches[0]["since"] is None
    expect(panel(page).locator(".sbar__uptime")).to_contain_text("Connected")
    assert page.dialogs == ["beforeunload"]  # type: ignore[attr-defined]  # the leave warning


def test_reload_brings_back_a_vanished_session_as_disconnected(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    connected(page, "build-box")
    gone = session_ids(page)[1]
    # The server forgets it entirely (expired while the page was away).
    page.evaluate(
        "(id) => { __pkWs.sessions.delete(id); const raw = JSON.parse(sessionStorage.getItem('__pkWsMock'));"
        " delete raw.sessions[id]; sessionStorage.setItem('__pkWsMock', JSON.stringify(raw)); }",
        gone,
    )
    page.reload()
    page.wait_for_selector(".app .tabstrip [role=tab]")
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(3)
    tab(page, "build-box").click()
    bar = panel(page).locator(".session__bottom")
    expect(bar).to_contain_text("Disconnected from build-box")
    expect(bar).to_contain_text("The session ended while this page was away.")
    tab(page, "web-prod").click()
    wait_connected(page)


def test_reload_restores_detached_sessions_nobody_remembers(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    page.evaluate("() => sessionStorage.removeItem('portkey.sessions')")
    page.reload()
    page.wait_for_selector(".app .tabstrip [role=tab]")
    expect(tab(page, "web-prod")).to_be_visible()
    tab(page, "web-prod").click()
    wait_connected(page)
    expect_rows(page, "Welcome to Ubuntu")
