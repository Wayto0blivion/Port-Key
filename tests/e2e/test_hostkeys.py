"""Host key dialogs in the browser (docs/ux-spec.md section 4) against the sshd test bed.

``sshd-rekeyed`` (host key set B) is not in the fixture known_hosts, so it is first seen. The
name ``localhost`` behind a jump host is the jump host itself: through ``sshd`` it presents set
A, through ``sshd-rekeyed`` set B. Trust is kept per route (the jump connection), so the two
routes are two hosts, each first seen once. A changed key needs one route to present a new key:
the spec points that route's jump connection at the other server, as when a jump host is
rebuilt. Each test forgets the records it relies on and resets what it changed, so reruns see
the same dialogs.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import (
    Api,
    active_panel,
    active_tab,
    connected,
    open_app,
    open_row,
    row_menu,
    tab_named,
    type_command,
)
from tests.fixtures import testbed as tb

FP_A, FP_B = tb.hostkey_fingerprint("a"), tb.hostkey_fingerprint("b")


@pytest.fixture
def api(app_api: AppApi, app_token: str) -> Api:
    api = Api(app_api, app_token)
    api.kill_sessions()
    return api


def stored(api: Api, host: str) -> list[str]:
    return [
        k["fp_sha256"] for k in api.call("GET", "/api/hostkeys")["host_keys"] if k["host"] == host
    ]


def test_first_seen_host_key(page: Page, base_url: str, app_token: str, api: Api) -> None:
    api.forget_host_keys(tb.SSHD_REKEYED.host)
    api.ensure("hk-rekeyed", host=tb.SSHD_REKEYED.host, username="keyonly")
    watch = open_app(page, base_url, app_token)

    # Cancel: nothing is trusted and the tab goes.
    open_row(page, "hk-rekeyed")
    dialog = page.get_by_role("dialog", name="Check this host's identity")
    expect(dialog).to_contain_text(
        "This is the first time Port-Key has connected to hk-rekeyed (sshd-rekeyed). "
        "It presented this key:"
    )
    expect(dialog).to_contain_text(f"ED25519 {FP_B}")
    expect(dialog).to_contain_text("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub")
    expect(dialog).to_contain_text("Nothing has been sent to the host yet.")
    dialog.get_by_role("button", name="Cancel").click()
    expect(tab_named(page, "hk-rekeyed")).to_have_count(0)
    assert stored(api, tb.SSHD_REKEYED.host) == []

    # Trust: stored, signed in, and not asked again. (A second click on the same row within
    # 500 ms counts as a double click, which the sidebar treats as one.)
    page.wait_for_timeout(600)
    open_row(page, "hk-rekeyed")
    page.get_by_role("dialog", name="Check this host's identity").get_by_role(
        "button", name="Trust this host"
    ).click()
    connected(page)
    type_command(page, "echo rk-$((40+2))", "rk-42")
    assert stored(api, tb.SSHD_REKEYED.host) == [FP_B]
    row_menu(page, "hk-rekeyed", "New session")
    connected(page)
    expect(page.get_by_role("dialog")).to_have_count(0)
    watch.check()


def test_changed_host_key_declined_then_replaced_by_name(
    page: Page, base_url: str, app_token: str, api: Api
) -> None:
    for host in ("localhost", tb.SSHD_REKEYED.host):
        api.forget_host_keys(host)
    via_a = api.ensure("hk-jump-a", host=tb.SSHD.host, username="keyonly")
    if via_a["host"] != tb.SSHD.host:  # a rerun: put the jump host back
        api.call("PATCH", f"/api/connections/{via_a['id']}", {"host": tb.SSHD.host})
    via_b = api.ensure("hk-jump-b", host=tb.SSHD_REKEYED.host, username="keyonly")
    api.ensure("hk-local-a", host="localhost", username="keyonly", jump_id=via_a["id"])
    api.ensure("hk-local-b", host="localhost", username="keyonly", jump_id=via_b["id"])
    watch = open_app(page, base_url, app_token)

    # localhost through sshd: first seen, set A.
    open_row(page, "hk-local-a")
    page.get_by_role("dialog", name="Check this host's identity").get_by_role(
        "button", name="Trust this host"
    ).click()
    connected(page)
    assert stored(api, "localhost") == [FP_A]

    # localhost through sshd-rekeyed: the jump host is first seen (named as the jump host),
    # then the target is first seen too. Another route is another host: no "changed" warning,
    # and the first route's key stays (review finding: one address behind two jump hosts).
    open_row(page, "hk-local-b")
    jump = page.get_by_role("dialog", name="Check the jump host's identity")
    expect(jump).to_contain_text("hk-local-b is reached through hk-jump-b (sshd-rekeyed).")
    expect(jump).to_contain_text("Jump host 1 of 1: hk-jump-b")
    jump.get_by_role("button", name="Trust this host").click()
    page.get_by_role("dialog", name="Check this host's identity").get_by_role(
        "button", name="Trust this host"
    ).click()
    connected(page)
    assert sorted(stored(api, "localhost")) == sorted([FP_A, FP_B])
    page.wait_for_timeout(600)
    open_row(page, "hk-local-a")  # focuses its open tab: still signed in, no prompt
    expect(page.get_by_role("dialog")).to_have_count(0)

    # hk-jump-a now leads to sshd-rekeyed (already trusted as a jump host): localhost on that
    # route presents set B where set A is trusted.
    api.call("PATCH", f"/api/connections/{via_a['id']}", {"host": tb.SSHD_REKEYED.host})
    row_menu(page, "hk-local-a", "New session")
    changed = page.get_by_role("dialog", name="hk-local-a's host key has changed")
    expect(changed).to_contain_text("Port-Key has sent no password and no key.")
    expect(changed).to_contain_text("Presented just now")
    expect(changed.locator(".fpcard").first).to_contain_text(re.compile(r"^Trusted since "))
    expect(changed.get_by_role("button", name="Keep it closed")).to_be_focused()
    replace = changed.get_by_role("button", name="Replace key and connect")
    expect(replace).to_be_disabled()

    # Declined: closed with nothing replaced; the tab offers Try again.
    changed.get_by_role("button", name="Keep it closed").click()
    panel = active_panel(page).locator(".session__panel")
    expect(panel).to_contain_text("host key has changed")
    assert sorted(stored(api, "localhost")) == sorted([FP_A, FP_B])

    # Confirmed by typing the connection's name (case and spaces do not matter).
    panel.get_by_role("button", name="Try again").click()
    changed = page.get_by_role("dialog", name="hk-local-a's host key has changed")
    field = changed.get_by_role("textbox")
    field.fill("hk-local")
    expect(replace).to_be_disabled()
    field.fill("  HK-Local-A ")
    expect(replace).to_be_enabled()
    replace.click()
    connected(page)
    expect(active_tab(page)).to_contain_text("hk-local-a")
    type_command(page, "echo via-b-$((40+2))", "via-b-42")
    assert stored(api, "localhost") == [FP_B, FP_B]  # both routes now lead to set B
    api.call("PATCH", f"/api/connections/{via_a['id']}", {"host": tb.SSHD.host})
    watch.check()
