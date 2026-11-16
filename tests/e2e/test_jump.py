"""A terminal through a jump host (docs/PLAN.md "Jump hosts"): ``sshd-inner`` is on the inner
network only, so the app reaches it just by tunnelling through ``sshd``."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import Api, active_panel, connected, open_app, open_row, type_command
from tests.fixtures import testbed as tb


def test_terminal_through_a_jump_host(
    page: Page, base_url: str, app_api: AppApi, app_token: str
) -> None:
    api = Api(app_api, app_token)
    api.kill_sessions()
    gateway = api.ensure("jump-gateway", host=tb.SSHD.host, username="keyonly")
    api.ensure("jump-inner", host=tb.SSHD_INNER.host, username="keyonly", jump_id=gateway["id"])
    watch = open_app(page, base_url, app_token)

    open_row(page, "jump-inner")
    connected(page)
    bar = active_panel(page).locator(".sbar")
    expect(bar.locator(".sbar__via")).to_have_text("via jump-gateway")
    expect(bar.locator(".sbar__where")).to_have_text(f"{tb.SSHD_INNER.host}:22")
    # sshd-inner is only reachable through the gateway, so a shell there proves the tunnel.
    type_command(page, "echo in-$((40+2))", "in-42")
    sessions = api.call("GET", "/api/sessions")["sessions"]
    assert [s["connection_id"] for s in sessions] == [api.connections()["jump-inner"]["id"]]

    # The gateway cannot be deleted while a connection jumps through it.
    r = app_api.request("DELETE", f"/api/connections/{gateway['id']}", token=app_token, json={})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "jump_in_use")
    watch.check()
