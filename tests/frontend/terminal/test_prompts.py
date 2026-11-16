"""Connection-time dialogs and failure panels in a terminal tab (package 8b): passphrase with
retry and remember, password and verification code, the changed host key with typed name,
the "refused your key" panel and its three actions, failures with Try again, and Cancel."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Page, expect

from tests.frontend.terminal.helpers import (
    TermFactory,
    connect,
    expect_rows,
    panel,
    sent,
    tab,
    wait_connected,
)


def replies(page: Page) -> list[dict[str, Any]]:
    return [m for m in sent(page) if m.get("type") == "prompt_reply"]


def test_passphrase_wrong_then_right(term: TermFactory) -> None:
    page = term()
    connect(page, "imaging-01")
    dialog = page.get_by_role("dialog", name="Unlock id_ed25519_portkey")
    expect(dialog).to_contain_text("Enter the passphrase for this key to connect to imaging-01.")
    keep = dialog.get_by_role("checkbox", name=re.compile("Keep unlocked for 8 hours"))
    expect(keep).to_be_checked()
    expect(dialog).to_contain_text("Held in memory only. Lock it any time from the top bar.")
    field = dialog.get_by_label("Passphrase")
    expect(field).to_be_focused()
    field.fill("wrong one")
    dialog.get_by_role("button", name="Unlock and connect").click()
    dialog = page.get_by_role("dialog", name="Unlock id_ed25519_portkey")
    expect(dialog.get_by_role("alert")).to_have_text(
        "That passphrase doesn't unlock id_ed25519_portkey. Try again."
    )
    expect(dialog.get_by_label("Passphrase")).to_have_value("")
    dialog.get_by_label("Passphrase").fill("open sesame")
    dialog.get_by_role("checkbox").uncheck()
    page.keyboard.press("Enter")
    wait_connected(page)
    r = replies(page)
    assert r[0] == {"type": "prompt_reply", "id": "p1", "value": "wrong one", "remember": True}
    assert r[1] == {"type": "prompt_reply", "id": "p2", "value": "open sesame", "remember": False}
    expect(panel(page).locator(".sbar__key")).to_have_text("id_ed25519_portkey")


def test_password_then_verification_code(term: TermFactory) -> None:
    page = term()
    connect(page, "print-srv")
    dialog = page.get_by_role("dialog", name="Sign in to print-srv")
    expect(dialog.get_by_label("Password for root@192.168.10.15")).to_be_focused()
    dialog.get_by_label("Password for root@192.168.10.15").fill("nope")
    dialog.get_by_role("button", name="Sign in").click()
    dialog = page.get_by_role("dialog", name="Sign in to print-srv")
    expect(dialog.get_by_role("alert")).to_have_text(
        "print-srv rejected the password for root. Check the user name and password, "
        "then try again."
    )
    dialog.get_by_label("Password for root@192.168.10.15").fill("pa55word")
    page.keyboard.press("Enter")
    code = page.get_by_role("dialog", name="Verification for print-srv")
    code.get_by_label("Verification code:").fill("492039")
    code.get_by_role("button", name="Continue").click()
    wait_connected(page)
    expect(panel(page).locator(".sbar__key")).to_have_text("Password")
    assert replies(page)[-1]["values"] == ["492039"]


def test_changed_host_key_keep_it_closed(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('web-prod', 'hostkey_changed')")
    connect(page, "web-prod")
    dialog = page.get_by_role("dialog", name="web-prod's host key has changed")
    expect(dialog).to_contain_text("doesn't match the one you trusted on 2 March 2026")
    expect(dialog).to_contain_text(
        "Port-Key has sent no password and no key. The connection is closed."
    )
    expect(dialog.locator(".fpcard__label")).to_have_text(
        ["Trusted since 2 March 2026", "Presented just now"]
    )
    expect(dialog.locator(".fp-strip")).to_have_count(2)
    keep = dialog.get_by_role("button", name="Keep it closed")
    expect(keep).to_be_focused()  # the safe button has initial focus
    replace = dialog.get_by_role("button", name="Replace key and connect")
    expect(replace).to_be_disabled()
    keep.click()
    heading = panel(page).get_by_role("heading", name="web-prod's host key has changed")
    expect(heading).to_be_visible()
    expect(panel(page).locator(".session__panel")).to_contain_text("sent no password and no key")
    assert replies(page)[-1] == {
        "type": "prompt_reply",
        "id": "p1",
        "accept": False,
        "confirm_name": "",
    }


def test_changed_known_hosts_key_says_the_old_line_stops_being_trusted(
    term: TermFactory,
) -> None:
    # Security review: replacing a key from known_hosts must not leave the owner thinking the
    # old line is gone. Port-Key distrusts it; the file itself is never edited.
    page = term()
    page.evaluate("__pkWs.behave('web-prod', 'hostkey_changed_known')")
    connect(page, "web-prod")
    dialog = page.get_by_role("dialog", name="web-prod's host key has changed")
    expect(dialog).to_contain_text("doesn't match the one in your known_hosts file")
    expect(dialog).to_contain_text("Replacing it makes Port-Key stop trusting the old key.")
    expect(dialog.locator("code")).to_have_text("ssh-keygen -R 192.168.10.42")
    dialog.get_by_role("button", name="Keep it closed").click()


def test_changed_host_key_from_the_app_store_has_no_known_hosts_note(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('web-prod', 'hostkey_changed')")
    connect(page, "web-prod")
    dialog = page.get_by_role("dialog", name="web-prod's host key has changed")
    expect(dialog).to_contain_text("Presented just now")
    expect(dialog).not_to_contain_text("ssh-keygen -R")
    dialog.get_by_role("button", name="Keep it closed").click()


def test_changed_host_key_replace_with_typed_name(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('web-prod', 'hostkey_changed')")
    connect(page, "web-prod")
    dialog = page.get_by_role("dialog", name="web-prod's host key has changed")
    field = dialog.get_by_role("textbox")
    replace = dialog.get_by_role("button", name="Replace key and connect")
    field.fill("web")
    expect(replace).to_be_disabled()
    field.fill("  WEB-PROD ")  # trimmed, ignoring case
    expect(replace).to_be_enabled()
    replace.click()
    wait_connected(page)
    assert replies(page)[-1] == {
        "type": "prompt_reply",
        "id": "p1",
        "accept": True,
        "confirm_name": "WEB-PROD",
    }


def test_key_refused_panel_and_password_this_time(term: TermFactory) -> None:
    page = term()
    connect(page, "backup-nas")
    p = panel(page).locator(".session__panel")
    expect(p.get_by_role("heading", name="backup-nas refused your key")).to_be_visible()
    expect(p).to_contain_text(
        "The server rejected id_ed25519 for admin. The key may have been removed from the "
        "host's authorized_keys, or the user name changed."
    )
    expect(p).to_contain_text("Server offered: publickey, password")
    expect(p.get_by_role("button")).to_have_text(
        ["Reinstall key", "Sign in with password this time", "Edit connection"]
    )
    expect(panel(page).locator(".sbar__status")).to_have_text("Not signed in")
    p.get_by_role("button", name="Sign in with password this time").click()
    dialog = page.get_by_role("dialog", name="Sign in to backup-nas")
    dialog.get_by_label("Password for admin@192.168.10.60").fill("pa55word")
    page.keyboard.press("Enter")
    wait_connected(page)
    opens = [m for m in sent(page) if m.get("type") == "open"]
    assert opens[0]["force_password"] is True


def test_key_refused_reinstall_opens_the_wizard_at_sign_in(term: TermFactory) -> None:
    page = term()
    connect(page, "backup-nas")
    panel(page).get_by_role("button", name="Reinstall key").click()
    wizard = page.get_by_role("dialog", name="Reinstall key on backup-nas")
    expect(wizard).to_be_visible()
    heading = wizard.get_by_role("heading", name="Sign in once")
    expect(heading).to_be_visible()  # the form is skipped
    start = page.evaluate("__pkWs.installs[0]")
    assert start["connection_id"]
    expected = {
        "host": "192.168.10.60",
        "username": "admin",
        "name": "backup-nas",
        "color": "purple",
    }
    assert {k: start[k] for k in expected} == expected


def test_key_refused_edit_connection(term: TermFactory) -> None:
    page = term()
    connect(page, "backup-nas")
    panel(page).get_by_role("button", name="Edit connection").click()
    expect(page.get_by_role("dialog", name="Edit backup-nas")).to_be_visible()


def test_timeout_panel_and_try_again(term: TermFactory) -> None:
    page = term()
    connect(page, "vpn-gw")
    p = panel(page).locator(".session__panel")
    expect(p.get_by_role("heading", name="Couldn't connect to vpn-gw")).to_be_visible()
    expect(p).to_contain_text(
        "Couldn't reach 10.8.0.1 on port 22. The connection timed out after 10 seconds."
    )
    expect(panel(page).locator(".session__overlay")).to_be_hidden()
    expect(tab(page, "vpn-gw").locator(".tab__dot")).to_have_class(re.compile("disconnected"))
    p.get_by_role("button", name="Try again").click()
    expect(p.get_by_role("heading", name="Couldn't connect to vpn-gw")).to_be_visible()
    assert len([m for m in sent(page, 0) if m.get("type") == "open"]) == 1
    assert len([m for m in sent(page, 1) if m.get("type") == "open"]) == 1


def test_connecting_checklist_and_cancel_closes_the_tab(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('slow', 'yes'); __pkWs.behave('web-prod', 'slow')")
    connect(page, "web-prod")
    overlay = panel(page).locator(".session__overlay")
    expect(overlay).to_contain_text("Connecting to web-prod…")
    expect(overlay.locator(".check-line").first).to_have_text("Reaching 192.168.10.42:22…")
    expect(tab(page, "web-prod").locator(".tab__dot")).to_have_class(
        re.compile("tab__dot--connecting")
    )
    expect(overlay.locator(".check-line--done").first).to_be_visible()
    overlay.get_by_role("button", name="Cancel").click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)


def test_cancel_in_a_prompt_closes_the_tab(term: TermFactory) -> None:
    page = term()
    connect(page, "imaging-01")
    page.get_by_role("dialog", name="Unlock id_ed25519_portkey").get_by_role(
        "button", name="Cancel"
    ).click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)
    assert replies(page)[-1] == {"type": "prompt_reply", "id": "p1", "cancel": True}


def test_too_many_sessions(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('web-prod', 'too_many')")
    connect(page, "web-prod")
    p = panel(page).locator(".session__panel")
    expect(p.get_by_role("heading", name="Too many sessions")).to_be_visible()
    expect(p).to_contain_text("Port-Key already has 32 sessions open.")
    p.get_by_role("button", name="Close tab").click()
    expect(page.locator(".tabstrip [role=tab]")).to_have_count(1)


def test_server_text_is_never_markup(term: TermFactory) -> None:
    page = term()
    page.evaluate("__pkWs.behave('print-srv', 'hostile')")
    connect(page, "print-srv")
    dialog = page.get_by_role("dialog", name="Sign in to print-srv")
    dialog.get_by_label("Password for root@192.168.10.15").fill("pa55word")
    page.keyboard.press("Enter")
    # Security review: the server's name was the dialog title, so a server could make this look
    # like Port-Key's own passphrase or sign-in dialog. The title is Port-Key's; the server's
    # text is framed as a message from that host, and each question names the host.
    code = page.get_by_role("dialog", name="Verification for print-srv")
    expect(code).to_be_visible()
    expect(code.locator(".prompt__hop")).to_contain_text("Server: print-srv")
    server = code.locator(".prompt__server")
    expect(server.locator(".prompt__server-label")).to_have_text("Message from print-srv:")
    expect(server.locator(".prompt__server-name")).to_have_text("<b>Bank</b> login")
    expect(server.locator(".prompt__instructions")).to_have_text(
        "<script>alert(1)</script> Enter the code."
    )
    expect(code.locator(".field__label")).to_have_text(
        "print-srv asks: <img src=x onerror=alert(1)>Code:"
    )
    expect(code.locator("img, b, script")).to_have_count(0)
    code.locator("input").fill("492039")
    page.keyboard.press("Enter")
    wait_connected(page)
    expect_rows(page, "root@print-srv")
