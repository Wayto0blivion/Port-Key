"""The key install wizard end to end against sshd (docs/ux-spec.md "Add connection").

- ``fresh`` (no ~/.ssh, password sign-in): a wrong password first, then the key is installed,
  verified with the key alone, and a terminal opens with it.
- ``tfa`` (keyboard-interactive: "Password: " then a TOTP "Verification code: ").
- ``keyonly`` with a key the server does not have: password login is disabled, so the wizard
  shows the manual install command and offers "Check key login".

Every test puts its sshd user back as the image had it, before and after.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import (
    Api,
    active_tab,
    connected,
    open_app,
    reset_ssh_user,
    toast,
    type_command,
)
from tests.fixtures import testbed as tb

STAMP = str(int(time.time()))[-6:]


@pytest.fixture
def api(app_api: AppApi, app_token: str) -> Api:
    api = Api(app_api, app_token)
    api.kill_sessions()
    return api


@pytest.fixture
def reset_fresh() -> Iterator[None]:
    reset_ssh_user("fresh")
    yield
    reset_ssh_user("fresh")


@pytest.fixture
def reset_tfa(totp: tb.Totp) -> Iterator[None]:
    reset_ssh_user("tfa", totp=totp)
    yield
    reset_ssh_user("tfa", totp=totp)


def start_wizard(page: Page, host: str, user: str, name: str) -> Locator:
    page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    wizard.locator("#wz-host").fill(host)
    wizard.locator("#wz-user").fill(user)
    wizard.locator("#wz-name").fill(name)
    return wizard


def test_wizard_installs_the_key_with_a_password(
    page: Page, base_url: str, app_token: str, api: Api, reset_fresh: None
) -> None:
    name = f"wz-fresh-{STAMP}"
    watch = open_app(page, base_url, app_token)
    wizard = start_wizard(page, tb.SSHD.host, "fresh", name)
    wizard.get_by_role("button", name="Next").click()

    password = wizard.locator("#wz-password:not([disabled])")
    expect(password).to_be_visible(timeout=20_000)
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()
    expect(wizard).to_contain_text("Host key matches your known_hosts entry.")
    expect(wizard.get_by_label(f"Password for fresh@{tb.SSHD.host}")).to_be_visible()
    password.fill("not the password")
    password.press("Enter")
    expect(wizard.get_by_role("alert")).to_contain_text(
        f"{name} rejected the password for fresh.", timeout=20_000
    )
    expect(password).to_have_value("")
    password.fill(tb.USERS["fresh"].password)
    wizard.get_by_role("button", name="Install key").click()

    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30_000)
    checklist = wizard.locator(".wcheck")
    for line in (
        "Signed in with password",
        "Added key to ~/.ssh/authorized_keys",
        "Signed in again with the key only",
        "Password discarded",
    ):
        expect(checklist).to_contain_text(line)
    expect(wizard.locator(".wsummary")).to_contain_text(f"fresh@{tb.SSHD.host}")
    expect(wizard).to_contain_text("From now on, one click opens this host with your key.")
    wizard.get_by_role("button", name="Open terminal").click()
    connected(page)
    expect(active_tab(page)).to_contain_text(name)
    type_command(page, "echo fr-$((40+2)); ls -ld ~/.ssh | cut -c1-10", "drwx------")

    saved = api.connections()[name]
    # Saved with the key it installed (the default key), not null: a later default-key change
    # must not switch it to a key the host does not have.
    default_fp = api.call("GET", "/api/keys")["default_fp"]
    assert (saved["auth_method"], saved["key_fp"], saved["username"]) == (
        "key",
        default_fp,
        "fresh",
    )
    watch.check()


def test_wizard_relays_the_2fa_prompts(
    page: Page, base_url: str, app_token: str, api: Api, totp: tb.Totp, reset_tfa: None
) -> None:
    name = f"wz-tfa-{STAMP}"
    watch = open_app(page, base_url, app_token)
    wizard = start_wizard(page, tb.SSHD.host, "tfa", name)
    wizard.get_by_role("button", name="Next").click()

    # tfa has no plain password method: the server's own prompts are relayed as fields.
    first = wizard.get_by_label("Password:")
    expect(first).to_be_enabled(timeout=20_000)
    first.fill(tb.USERS["tfa"].password)
    first.press("Enter")
    code = wizard.get_by_label("Verification code:")
    expect(code).to_be_enabled(timeout=15_000)
    expect(wizard).to_contain_text("Password sent")
    code.fill(totp.fresh(min_remaining=8.0))
    code.press("Enter")
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30_000)
    expect(wizard.locator(".wcheck")).to_contain_text("Signed in again with the key only")

    # From now on the key alone signs in: no prompt at all.
    wizard.get_by_role("button", name="Open terminal").click()
    connected(page)
    expect(page.get_by_role("dialog")).to_have_count(0)
    type_command(page, "echo tf-$((40+2))", "tf-42")
    assert api.connections()[name]["auth_method"] == "key"
    watch.check()


def test_password_login_disabled_shows_the_manual_command(
    page: Page, base_url: str, app_token: str, api: Api
) -> None:
    name = f"wz-keyonly-{STAMP}"
    unknown = api.key("id_ed25519_portkey")  # the one fixture key keyonly does not have
    watch = open_app(page, base_url, app_token)
    wizard = start_wizard(page, tb.SSHD.host, "keyonly", name)
    wizard.locator("#wz-auth").select_option(label="id_ed25519_portkey")
    wizard.get_by_role("button", name="Next").click()

    expect(wizard.get_by_role("heading", name=f"{name} does not accept passwords")).to_be_visible(
        timeout=20_000
    )
    expect(wizard).to_contain_text("The server only allows key logins")
    command = wizard.get_by_label(f"Run this on {name} as keyonly")
    expect(command).to_have_value(
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{unknown['public_key']}' >> "
        "~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )
    wizard.get_by_role("button", name="Copy install command").click()
    toast(page, "Install command copied")
    copied = page.evaluate("() => navigator.clipboard.readText()")
    assert copied == command.input_value()
    wizard.get_by_role("button", name="Copy public key only").click()
    toast(page, "Public key copied")
    assert page.evaluate("() => navigator.clipboard.readText()") == unknown["public_key"]

    # Nothing changed on the host, so checking again finds the key still refused.
    wizard.get_by_role("button", name="Check key login").click()
    expect(wizard.get_by_role("heading", name=f"{name} does not accept passwords")).to_be_visible(
        timeout=20_000
    )
    expect(wizard.get_by_role("button", name="Check key login")).to_be_enabled()
    wizard.get_by_role("button", name="Cancel").click()
    expect(wizard).to_have_count(0)
    assert name not in api.connections()
    watch.check()
