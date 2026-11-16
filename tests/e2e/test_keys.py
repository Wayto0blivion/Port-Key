"""Keys in the browser: copying the public key, a passphrase-protected key unlocked from the
connect prompt and locked from the top bar, and a key generated in Port-Key then installed on a
host with the wizard (docs/ux-spec.md "Keys tab", "Passphrase prompt")."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import (
    Api,
    active_tab,
    connected,
    open_app,
    open_row,
    reset_ssh_user,
    row_menu,
    tab_named,
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


def open_keys(page: Page, name: str) -> None:
    page.locator(".topbar").get_by_role("button", name="Keys", exact=True).click()
    page.locator(".table--keys .name-btn", has_text=name).first.click()
    expect(page.locator(".key-detail .key-detail__name")).to_have_text(name)


def key_row_passphrase(page: Page, name: str) -> str:
    row = page.locator(".table--keys tbody tr").filter(
        has=page.locator(f".name-btn:text-is('{name}')")
    )
    return row.locator("td").nth(4).inner_text()


def clipboard(page: Page) -> str:
    return str(page.evaluate("() => navigator.clipboard.readText()"))


def test_copy_the_public_key(page: Page, base_url: str, app_token: str, api: Api) -> None:
    key = api.key("id_rsa")  # the PowerShell-written UTF-16 .pub with CRLF
    watch = open_app(page, base_url, app_token)
    open_keys(page, "id_rsa")
    detail = page.locator(".key-detail")
    expect(detail).to_contain_text("C:\\Users\\john\\.ssh\\id_rsa")
    expect(detail.get_by_label("Public key")).to_have_value(key["public_key"])
    detail.get_by_role("button", name="Copy public key").click()
    toast(page, "Public key copied")
    assert clipboard(page) == key["public_key"]
    assert "\r" not in key["public_key"]
    assert key["public_key"].startswith("ssh-rsa AAAA")
    detail.get_by_role("button", name="Copy install command").click()
    toast(page, "Install command copied")
    assert f"echo '{key['public_key']}' >> ~/.ssh/authorized_keys" in clipboard(page)
    # The same text over the API, as text/plain with one trailing line feed.
    assert api.call("GET", f"/api/keys/{key['id']}/public") == key["public_key"] + "\n"
    watch.check()


def test_passphrase_key_unlock_and_lock(
    page: Page, base_url: str, app_token: str, api: Api
) -> None:
    api.call("POST", "/api/keys/lock")
    locked = api.key("id_ed25519_pass")
    api.ensure("keys-locked", host=tb.SSHD.host, username="keyonly", key_fp=locked["fp"])
    watch = open_app(page, base_url, app_token)
    indicator = page.locator(".topbar .unlocked")
    expect(indicator).to_be_hidden()

    open_row(page, "keys-locked")
    dialog = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
    expect(dialog).to_contain_text("Enter the passphrase for this key to connect to keys-locked.")
    expect(dialog.get_by_label("Keep unlocked for 8 hours")).to_be_checked()
    dialog.get_by_label("Passphrase").fill("not the passphrase")
    dialog.get_by_role("button", name="Unlock and connect").click()
    dialog = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
    expect(dialog.get_by_role("alert")).to_contain_text(
        "That passphrase doesn't unlock id_ed25519_pass. Try again.", timeout=15_000
    )
    expect(dialog.get_by_label("Passphrase")).to_have_value("")
    dialog.get_by_label("Passphrase").fill(tb.KEY_PASSPHRASE)
    dialog.get_by_role("button", name="Unlock and connect").click()
    connected(page)
    type_command(page, "echo pp-$((40+2))", "pp-42")
    expect(indicator).to_have_text("1 key unlocked")
    assert api.key("id_ed25519_pass")["unlocked"] is True

    # Held: a second session asks nothing.
    row_menu(page, "keys-locked", "New session")
    connected(page)
    expect(page.get_by_role("dialog")).to_have_count(0)
    open_keys(page, "id_ed25519_pass")
    assert key_row_passphrase(page, "id_ed25519_pass") == "Yes, unlocked"

    # Lock from the top bar: the indicator goes and the next connect asks again.
    page.locator(".topbar").get_by_role("button", name="Lock", exact=True).click()
    expect(indicator).to_be_hidden()
    expect(page.locator(".table--keys")).to_contain_text("Yes, locked")
    assert api.key("id_ed25519_pass")["unlocked"] is False
    row_menu(page, "keys-locked", "New session")
    dialog = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(tab_named(page, "keys-locked · 3")).to_have_count(0)
    # Wrong passphrases answer 400 bad_passphrase on the socket path, never a sign-out.
    watch.check()


@pytest.fixture
def fresh_user() -> Iterator[None]:
    reset_ssh_user("fresh")
    yield
    reset_ssh_user("fresh")


def test_generate_a_key_and_install_it(
    page: Page, base_url: str, app_token: str, api: Api, fresh_user: None
) -> None:
    key_name, conn_name = f"id_e2e_{STAMP}", f"keys-fresh-{STAMP}"
    watch = open_app(page, base_url, app_token)
    page.locator(".topbar").get_by_role("button", name="Keys", exact=True).click()
    page.locator(".page__head").get_by_role("button", name="Generate key").click()
    gen = page.get_by_role("dialog", name="Generate key")
    gen.get_by_label("File name").fill(key_name)
    expect(gen).to_contain_text("Name is free.")
    gen.get_by_role("button", name="Generate key").click()
    expect(gen).to_have_count(0)
    detail = page.locator(".key-detail")
    expect(detail.locator(".key-detail__name")).to_have_text(key_name)
    expect(detail).to_contain_text(f"C:\\Users\\john\\.ssh\\port-key\\{key_name}")
    made = api.key(key_name)
    assert (made["source"], made["type"], made["encrypted"]) == ("generated", "ed25519", False)

    detail.get_by_role("button", name="Install on a host…").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    expect(wizard.locator("#wz-auth option:checked")).to_have_text(key_name)
    wizard.locator("#wz-host").fill(tb.SSHD.host)
    wizard.locator("#wz-user").fill("fresh")
    wizard.locator("#wz-name").fill(conn_name)
    wizard.get_by_role("button", name="Next").click()
    password = wizard.locator("#wz-password:not([disabled])")
    expect(password).to_be_visible(timeout=20_000)
    expect(wizard).to_contain_text(key_name)  # "It adds the public half of <key>"
    password.fill(tb.USERS["fresh"].password)
    wizard.get_by_role("button", name="Install key").click()
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30_000)
    for line in (
        "Signed in with password",
        "Added key to ~/.ssh/authorized_keys",
        "Signed in again with the key only",
        "Password discarded",
    ):
        expect(wizard.locator(".wcheck")).to_contain_text(line)
    expect(wizard.locator(".wsummary")).to_contain_text(key_name)
    wizard.get_by_role("button", name="Open terminal").click()
    connected(page)
    expect(active_tab(page)).to_contain_text(conn_name)
    type_command(page, "grep -c port-key ~/.ssh/authorized_keys | sed s/^/n-/", "n-1")

    saved = api.connections()[conn_name]
    assert (saved["auth_method"], saved["key_fp"]) == ("key", made["fp"])
    assert [u["name"] for u in api.key(key_name)["used_by"]] == [conn_name]
    watch.check()
