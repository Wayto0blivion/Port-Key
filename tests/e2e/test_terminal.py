"""Terminal sessions in a real browser against sshd (docs/ux-spec.md "Terminal view").

Covers typing a command and reading xterm's rendered rows, two sessions to one host, a reload
that reattaches and replays, and the Content-Security-Policy holding while xterm renders: every
spec here fails on a console error, and ``CSP_WATCH`` turns each CSP violation into one. The
WebSocket itself is the proof that ``connect-src 'self'`` covers the same-origin ``ws://``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import (
    Api,
    active_panel,
    connected,
    open_app,
    open_row,
    row_menu,
    rows,
    sidebar_row,
    tab_named,
    type_command,
)
from tests.fixtures import testbed as tb

NAME = "term-keyonly"


@pytest.fixture
def api(app_api: AppApi, app_token: str) -> Api:
    api = Api(app_api, app_token)
    api.kill_sessions()
    return api


@pytest.fixture
def conn(api: Api) -> dict[str, Any]:
    return api.ensure(NAME, host=tb.SSHD.host, username="keyonly", color="red")


def test_type_a_command_and_read_the_rows(
    page: Page, base_url: str, app_token: str, conn: dict[str, Any]
) -> None:
    watch = open_app(page, base_url, app_token)
    open_row(page, NAME)
    connected(page)
    bar = active_panel(page).locator(".sbar")
    expect(bar).to_contain_text("keyonly@")
    expect(bar.locator(".sbar__where")).to_have_text(f"{tb.SSHD.host}:22")
    expect(bar.locator(".sbar__key")).to_have_text("id_ed25519")
    expect(active_panel(page).locator(".xterm-helper-textarea")).to_be_focused()
    type_command(page, "echo x-$((6*7))", "x-42")
    type_command(page, "whoami | tr a-z A-Z", "KEYONLY")
    watch.check()


def test_xterm_renders_colour_under_the_csp(
    page: Page, base_url: str, app_token: str, conn: dict[str, Any]
) -> None:
    watch = open_app(page, base_url, app_token)
    open_row(page, NAME)
    connected(page)
    # 24-bit colour makes the DOM renderer set style attributes, the case the CSP's
    # style-src 'unsafe-inline' exists for. The marker is computed so the echo cannot match.
    type_command(page, r"printf '\033[38;2;255;0;0mrgb-%s\033[0m\n' $((40+2))", "rgb-42")
    styled = rows(page).locator("span", has_text="rgb-42").first
    expect(styled).to_have_attribute("style", re.compile(r"color:\s*#ff0000"))
    expect(styled).to_have_css("color", "rgb(255, 0, 0)")
    watch.check()


def test_two_sessions_to_one_host(
    page: Page, base_url: str, app_token: str, api: Api, conn: dict[str, Any]
) -> None:
    watch = open_app(page, base_url, app_token)
    open_row(page, NAME)
    connected(page)
    type_command(page, "echo one-$((1+1))", "one-2")
    # A click on a connection with an open session focuses it; "New session" adds another.
    row_menu(page, NAME, "New session")
    expect(tab_named(page, f"{NAME} · 2")).to_have_attribute("aria-selected", "true")
    connected(page)
    expect(rows(page)).not_to_contain_text("one-2")  # a fresh shell
    type_command(page, "echo two-$((1+2))", "two-3")
    expect(sidebar_row(page, NAME).first.locator(".badge")).to_contain_text("2")
    sessions = api.call("GET", "/api/sessions")["sessions"]
    assert [s["connection_id"] for s in sessions] == [conn["id"], conn["id"]]
    assert all(s["attached"] for s in sessions)

    open_row(page, NAME)  # focuses an open session, adds none
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(3)  # Home + 2
    tab_named(page, NAME).click()
    expect(rows(page)).to_contain_text("one-2")
    watch.check()


def test_reload_reattaches_and_replays(
    page: Page, base_url: str, app_token: str, api: Api, conn: dict[str, Any]
) -> None:
    watch = open_app(page, base_url, app_token)
    open_row(page, NAME)
    connected(page)
    type_command(page, "echo before-$((20+1)); cd /tmp", "before-21")
    [before] = api.call("GET", "/api/sessions")["sessions"]

    page.reload()
    page.wait_for_selector(".app .tabstrip [role=tab]")
    expect(tab_named(page, NAME)).to_have_attribute("aria-selected", "true")
    connected(page)
    expect(rows(page)).to_contain_text("before-21")  # replayed from the ring buffer
    type_command(page, "echo after-$((20+2)) $PWD", "after-22 /tmp")  # the same shell
    [after] = api.call("GET", "/api/sessions")["sessions"]
    assert after["session_id"] == before["session_id"]
    assert after["attached"] is True
    watch.check()
