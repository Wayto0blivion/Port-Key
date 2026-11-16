"""Adding a connection from a known_hosts suggestion (docs/ux-spec.md "Suggestions").

The fixture known_hosts lists ``[sshd]:2222`` (host key set A). [Add] opens the wizard with
the host filled in; the host key matches known_hosts, so step 2 needs no decision; keyonly
already has the key, so the wizard saves at once ("already installed"). Then the suggestion is
gone, because a saved connection uses that host and port.

Sorts early so it can also see the first-run suggestion list; on a rerun it deletes the
connection it made (and the other spec's one on sshd:2222) so the suggestion comes back.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import Api, active_panel, open_app, sidebar_row
from tests.fixtures import testbed as tb

NAME = "suggested-2222"
LABEL = f"{tb.SSHD_ALT_PORT.host}:{tb.SSHD_ALT_PORT.port}"


def test_add_from_a_known_hosts_suggestion(
    page: Page, base_url: str, app_api: AppApi, app_token: str
) -> None:
    api = Api(app_api, app_token)
    for name, conn in api.connections().items():
        if conn["host"] == tb.SSHD_ALT_PORT.host and conn["port"] == tb.SSHD_ALT_PORT.port:
            api.delete_connection(name)
    watch = open_app(page, base_url, app_token)

    home = active_panel(page)
    add = home.get_by_role("button", name=f"Add {LABEL}", exact=True)
    expect(home.locator(".suggestion").first).to_be_visible()
    if not add.is_visible():  # the first-run card shows three; the rest behind "See all"
        home.get_by_role("button", name=re.compile(r"^See all \d+$")).click()
    add.click()

    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard.locator("#wz-host")).to_have_value(tb.SSHD_ALT_PORT.host)
    expect(wizard.locator("#wz-port")).to_have_value(str(tb.SSHD_ALT_PORT.port))
    wizard.locator("#wz-user").fill("keyonly")
    wizard.locator("#wz-name").fill(NAME)
    wizard.get_by_role("button", name="Next").click()

    expect(wizard).to_contain_text(
        f"Your key is already installed on {NAME}. Port-Key signed in with it", timeout=20_000
    )
    wizard.get_by_role("button", name="Save connection").click()
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
    expect(wizard.locator(".wsummary")).to_contain_text(f"keyonly@{LABEL}")
    expect(wizard.locator(".wsummary")).to_contain_text("id_ed25519")
    page.keyboard.press("Escape")
    expect(wizard).to_have_count(0)

    saved = api.connections()[NAME]
    assert (saved["host"], saved["port"], saved["username"]) == ("sshd", 2222, "keyonly")
    assert (saved["auth_method"], saved["key_fp"]) == ("key", None)
    expect(sidebar_row(page, NAME)).to_have_count(1)
    expect(home.get_by_role("button", name=f"Add {LABEL}", exact=True)).to_have_count(0)
    suggestions = api.call("GET", "/api/hosts/suggestions")["suggestions"]
    assert (tb.SSHD_ALT_PORT.host, tb.SSHD_ALT_PORT.port) not in [
        (s["host"], s["port"]) for s in suggestions
    ]
    watch.check()
