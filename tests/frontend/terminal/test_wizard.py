"""The key install wizard over /ws/install (package 8b): the four steps, the first-seen host
key inline, password and 2FA fields, the live checklist, the final card, Password only, the
entry points the shell calls, and Open terminal. Failure states are in test_wizard_failures.py."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Locator, Page, expect

from tests.frontend.terminal.helpers import TermFactory, sent, tab, wait_connected


def open_wizard(page: Page) -> Locator:
    page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard).to_be_visible()
    return wizard


def fill_host(wizard: Locator, host: str) -> None:
    wizard.get_by_label("Host", exact=True).fill(host)


def start_msg(page: Page) -> dict[str, Any]:
    msgs = sent(page, -1, "/ws/install")
    assert msgs[0]["type"] == "auth"
    result: dict[str, Any] = msgs[1]
    return result


def steps(wizard: Locator) -> Locator:
    return wizard.locator(".wsteps__item")


def test_step_one_fields_and_defaults(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    expect(steps(wizard)).to_have_text(["1 Host", "2 Identity", "3 Sign in", "4 Verify"])
    expect(wizard.locator(".wsteps__item.is-current")).to_have_text("1 Host")
    expect(wizard.get_by_label("Host", exact=True)).to_be_focused()
    expect(wizard.get_by_label("Port", exact=True)).to_have_value("22")
    expect(wizard.get_by_label("User", exact=True)).to_have_value("john")
    expect(wizard).to_contain_text("Name or IP address, as reachable from this PC.")
    expect(wizard.get_by_label("Sign in with")).to_have_value("")
    expect(wizard.get_by_label("Sign in with").locator("option")).to_have_text(
        ["Default key (id_ed25519)", "id_rsa", "id_ed25519_portkey", "Password only"]
    )
    expect(wizard.get_by_label("Jump via").locator("option").first).to_have_text(
        "None, connect directly"
    )
    expect(wizard.get_by_label("Folder").locator("option").first).to_have_text("Ungrouped")
    fill_host(wizard, "lab-01.office.lan")
    expect(wizard.get_by_label("Name", exact=True)).to_have_value("lab-01")  # filled from the host
    wizard.get_by_label("Name", exact=True).fill("lab one")
    fill_host(wizard, "lab-02")
    expect(wizard.get_by_label("Name", exact=True)).to_have_value("lab one")  # not after an edit


def test_validation(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    wizard.get_by_role("button", name="Next").click()
    expect(wizard.locator("#wz-host-error")).to_have_text("Enter a host name or IP address.")
    expect(wizard.get_by_label("Host", exact=True)).to_be_focused()
    fill_host(wizard, "192.168.10.99")
    wizard.get_by_label("Name", exact=True).fill("Web-Prod")
    wizard.get_by_role("button", name="Next").click()
    expect(wizard.locator("#wz-name-error")).to_have_text(
        "A connection named web-prod already exists. Choose another name."
    )
    assert page.evaluate("__pkWs.installs.length") == 0


def test_install_with_password(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "lab-01")
    wizard.get_by_label("Folder").select_option(label="Office LAN / Servers")
    wizard.locator("#wz-tags").fill("lab,")
    wizard.get_by_label("Blue").check()
    wizard.get_by_role("button", name="Next").click()
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()
    expect(wizard.locator(".wsteps__item.is-current")).to_have_text("3 Sign in")
    expect(steps(wizard).nth(0)).to_have_text("✓ Host, done")
    expect(wizard).to_contain_text("✓ Host key matches your known_hosts entry.")
    expect(wizard).to_contain_text(
        "Your key is not on lab-01 yet. Enter the password for john and Port-Key installs it "
        "for you."
    )
    expect(wizard).to_contain_text(
        "It adds the public half of id_ed25519 to ~/.ssh/authorized_keys on the host."
    )
    field = wizard.get_by_label("Password for john@lab-01")
    expect(field).to_be_focused()
    field.fill("wrong")
    wizard.get_by_role("button", name="Install key").click()
    expect(wizard.get_by_role("alert")).to_have_text(
        "lab-01 rejected the password for john. Check the user name and password, then try again."
    )
    expect(field).to_have_value("")
    expect(field).to_be_focused()
    field.fill("pa55word")
    page.keyboard.press("Enter")
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
    expect(wizard.locator(".wcheck__text")).to_have_text(
        [
            "Signed in with password, done",
            "Added key to ~/.ssh/authorized_keys, done",
            "Signed in again with the key only, done",
            "Password discarded, done",
        ]
    )
    expect(wizard.locator(".wsummary")).to_contain_text("lab-01")
    expect(wizard.locator(".wsummary__addr")).to_have_text("john@lab-01 · Office LAN / Servers")
    expect(wizard.locator(".wsummary__key")).to_have_text("id_ed25519")
    expect(wizard).to_contain_text(
        "From now on, one click opens this host with your key. No password needed."
    )
    open_btn = wizard.get_by_role("button", name="Open terminal")
    expect(open_btn).to_be_focused()
    start = start_msg(page)
    assert start["group_id"]
    expected = {
        "type": "start", "host": "lab-01", "port": 22, "username": "john", "name": "lab-01",
        "key_fp": None, "tags": ["lab"], "color": "blue", "assume_installed": False,
        "connection_id": None, "jump_id": None,
    }  # fmt: skip
    assert {k: start[k] for k in expected} == expected
    replies = [m for m in sent(page, -1, "/ws/install") if m.get("type") == "prompt_reply"]
    assert [r["value"] for r in replies] == ["wrong", "pa55word"]
    expect(page.locator(".sidebar")).to_contain_text("lab-01")  # the store refreshed
    open_btn.click()
    expect(wizard).to_be_hidden()
    expect(tab(page, "lab-01")).to_be_visible()
    wait_connected(page)


def test_first_seen_host_key_is_step_two(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "new-host")
    wizard.get_by_role("button", name="Next").click()
    expect(wizard.get_by_role("heading", name="Check this host's identity")).to_be_visible()
    expect(wizard.locator(".wsteps__item.is-current")).to_have_text("2 Identity")
    expect(wizard).to_contain_text(
        "This is the first time Port-Key has connected to new-host (new-host). "
        "It presented this key:"
    )
    expect(wizard.locator(".fpbox__text")).to_have_text(
        "ED25519 SHA256:Qd03vLxB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6h7Rk"
    )
    expect(wizard).to_contain_text("Nothing has been sent to the host yet.")
    expect(wizard.locator(".wizard__foot button")).to_have_text(
        ["Back", "Cancel", "Trust this host"]
    )
    wizard.get_by_role("button", name="Back").click()
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("new-host")  # values kept
    wizard.get_by_role("button", name="Next").click()
    wizard.get_by_role("button", name="Trust this host").click()
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()
    expect(wizard).to_contain_text("✓ Host key trusted.")


def test_two_factor_fields(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "tfa-host")
    wizard.get_by_role("button", name="Next").click()
    code = wizard.get_by_label("Password:")
    expect(code).to_be_focused()
    expect(wizard.get_by_label("Password for john@tfa-host")).to_be_hidden()
    code.fill("pa55word")
    wizard.get_by_role("button", name="Install key").click()
    second = wizard.get_by_label("Verification code:")
    expect(second).to_be_focused()
    second.fill("492039")
    page.keyboard.press("Enter")
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()


def test_key_already_installed(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "keyed-host")
    wizard.get_by_role("button", name="Next").click()
    expect(wizard).to_contain_text(
        "Your key is already installed on keyed-host. Port-Key signed in with it, "
        "so no password is needed."
    )
    wizard.get_by_role("button", name="Save connection").click()
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
    wizard.get_by_role("button", name="Add another").click()
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("")
    expect(wizard.get_by_label("Host", exact=True)).to_be_focused()


def test_password_only_saves_over_http(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "old-switch")
    wizard.get_by_label("Sign in with").select_option(label="Password only")
    expect(wizard.get_by_label("My key is already on this host")).to_be_hidden()
    wizard.get_by_role("button", name="Save connection").click()
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
    expect(wizard).to_contain_text("old-switch is saved with password sign-in.")
    expect(wizard.locator(".wsummary__pw")).to_have_text("Password")
    post = page.evaluate(
        "__pkMock.calls.filter((c) => c.method === 'POST' && c.path === '/api/connections')"
    )
    assert post[-1]["body"]["auth_method"] == "password"
    assert page.evaluate("__pkWs.installs.length") == 0


def test_prefill_from_a_suggestion_and_from_keys(term: TermFactory) -> None:
    page = term()
    page.click(".sidebar__suggest-head")
    page.locator(".sidebar").get_by_role("button", name=re.compile(r"^Add lab-01")).first.click()
    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("lab-01")
    expect(wizard.get_by_label("Name", exact=True)).to_have_value("lab-01")
    wizard.get_by_role("button", name="Cancel").click()
    expect(wizard).to_be_hidden()
    page.get_by_role("button", name="Keys", exact=True).click()
    page.locator(".table--keys").get_by_role("button", name="id_ed25519_portkey").click()
    page.get_by_role("button", name="Install on a host…").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard.get_by_label("Sign in with")).to_have_value(
        "SHA256:9dTgV4hY6jU8kI0oL2pM4nB6vC8xZ0aS2dF4gH6mE1s"
    )


def test_jump_via_and_cancel_mid_run(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "lab-01")
    wizard.get_by_label("Jump via").select_option(label="build-box")
    wizard.get_by_role("button", name="Next").click()
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()
    start = start_msg(page)
    assert start["jump_id"] == page.evaluate(
        "__pkMockData('normal').connections.find((c) => c.name === 'build-box').id"
    )
    wizard.get_by_role("button", name="Cancel").click()
    expect(wizard).to_be_hidden()
    assert sent(page, -1, "/ws/install")[-1] == {"type": "close"}
    expect(
        page.locator(".sidebar__foot").get_by_role("button", name="Add connection")
    ).to_be_focused()


def test_changed_host_key_in_the_wizard(term: TermFactory) -> None:
    page = term()
    wizard = open_wizard(page)
    fill_host(wizard, "changed-host")
    wizard.get_by_role("button", name="Next").click()
    dialog = page.get_by_role("dialog", name="changed-host's host key has changed")
    expect(dialog).to_contain_text("doesn't match the one in your known_hosts file")
    expect(dialog.locator(".fpcard__label").first).to_have_text("In your known_hosts file")
    dialog.get_by_role("textbox").fill("changed-host")
    dialog.get_by_role("button", name="Replace key and connect").click()
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()


def test_a_wizard_opened_before_the_keys_arrive_still_offers_the_key(term: TermFactory) -> None:
    # Found end to end (package 9): Add connection right after a page load built the form with
    # no keys, said "Port-Key has no key to install" and chose Password only.
    page = term()
    page.evaluate(
        """async () => {
          const s = await import('/js/store.js');
          Object.assign(s.getState(), {keys: []});
          s.getState().status.keys = 'idle';
        }"""
    )
    wizard = open_wizard(page)
    expect(wizard.locator("#wz-auth option:checked")).to_have_text(re.compile(r"^Default key \("))
    expect(wizard).not_to_contain_text("Port-Key has no key to install")
