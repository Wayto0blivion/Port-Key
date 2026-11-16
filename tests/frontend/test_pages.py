"""Keys, Settings and Diagnostics tabs (package 8a)."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.frontend.conftest import AppFactory, calls, expect_toast

PASSWORD = "correct horse battery"


def open_keys(page: Page) -> None:
    page.get_by_role("button", name="Keys", exact=True).click()
    expect(page.locator(".key-detail")).to_be_visible()


def key_names(page: Page) -> list[str]:
    names: list[str] = page.locator(".table--keys .name-btn").all_text_contents()
    return names


def test_keys_table_and_detail(app: AppFactory) -> None:
    page = app()
    open_keys(page)
    assert key_names(page) == ["id_ed25519", "id_rsa", "id_ed25519_portkey"]
    rows = page.locator(".table--keys tbody tr")
    expect(rows.nth(0)).to_contain_text("Default")
    expect(rows.nth(1)).to_contain_text("RSA 4096")
    expect(rows.nth(1)).to_contain_text("Not used")
    expect(rows.nth(2)).to_contain_text("Made in Port-Key")
    expect(rows.nth(2)).to_contain_text("Yes, unlocked")
    expect(rows.nth(0).locator(".fp-strip")).to_have_attribute(
        "aria-label", "Colour strip for SHA256:Xk3fP0…r9Qa"
    )
    detail = page.locator(".key-detail")
    expect(detail).to_contain_text("C:\\Users\\john\\.ssh\\id_ed25519")
    expect(detail.locator(".used-by button")).to_have_text(
        ["web-prod", "build-box", "backup-nas", "parser-vm", "vpn-gw"]
    )
    detail.get_by_role("button", name="Copy install command").click()
    expect_toast(page, "Install command copied")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.startswith("mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo 'ssh-ed25519 AAAA")
    assert copied.endswith(">> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
    page.locator(".table--keys .name-btn", has_text="id_ed25519").first.focus()
    page.keyboard.press("ArrowDown")
    expect(page.locator(".table--keys .name-btn", has_text="id_rsa")).to_be_focused()
    expect(detail.get_by_role("heading")).to_have_text("id_rsa")
    detail.get_by_role("button", name="Set as default").click()
    expect_toast(page, "id_rsa is now the default key.")
    expect(rows.nth(1)).to_contain_text("Default")
    expect(page.locator(".key-detail").get_by_role("button", name="Default key")).to_be_disabled()


def test_generate_key(app: AppFactory) -> None:
    page = app()
    open_keys(page)
    page.locator(".page__head").get_by_role("button", name="Generate key").click()
    dialog = page.get_by_role("dialog", name="Generate key")
    name = dialog.get_by_label("File name")
    expect(name).to_have_value("id_ed25519_portkey_2")  # the default name is taken
    expect(dialog).to_contain_text(
        "Saved to C:\\Users\\john\\.ssh\\port-key. Port-Key never overwrites an existing file."
    )
    name.fill("id_rsa")  # in .ssh, not in .ssh\port-key: free there
    expect(dialog).to_contain_text("Name is free.")
    name.fill("ID_ED25519_PORTKEY")  # the generated-keys folder clashes ignoring case
    expect(dialog.locator("#gk-name-error")).to_have_text(
        "A key named ID_ED25519_PORTKEY already exists. Choose another name."
    )
    name.fill("bad name.pub")
    expect(dialog.locator("#gk-name-error")).to_contain_text(
        "Use letters, digits, dot, dash and underscore"
    )
    name.fill("id_lab")
    expect(dialog.locator("#gk-name-error")).to_be_hidden()
    dialog.get_by_label("RSA 4096").check()
    expect(name).to_have_value("id_lab")  # a typed name stays when the type changes
    dialog.get_by_label("Passphrase (optional)").fill("one passphrase")
    dialog.get_by_label("Confirm passphrase").fill("another")
    dialog.get_by_role("button", name="Generate key").click()
    expect(dialog.locator("#gk-pass2-error")).to_have_text("The passphrases don't match.")
    dialog.get_by_label("Confirm passphrase").fill("one passphrase")
    dialog.get_by_role("button", name="Generate key").click()
    expect(dialog).to_have_count(0)
    expect_toast(page, "Generated id_lab.")
    assert "id_lab" in key_names(page)
    gen = [c for c in calls(page) if c["path"] == "/api/keys/generate"][-1]
    assert gen["body"]["type"] == "rsa"
    assert gen["body"]["bits"] == 4096
    assert (
        "passphrase" not in gen["body"]
    )  # the mock drops secrets from its log; the client sent one
    expect(page.locator(".key-detail .key-detail__name")).to_have_text("id_lab")


def test_hide_a_used_key_and_unhide(app: AppFactory) -> None:
    page = app()
    open_keys(page)
    page.locator(".table--keys .name-btn", has_text="id_ed25519_portkey").click()
    page.locator(".key-detail").get_by_role("button", name="Hide from Port-Key").click()
    dialog = page.get_by_role("dialog", name="Hide id_ed25519_portkey")
    expect(dialog).to_contain_text(
        "id_ed25519_portkey is used by 1 connection: imaging-01. Choose what they use instead:"
    )
    dialog.get_by_label("Sign in with").select_option(label="Password only")
    dialog.get_by_role("button", name="Hide key").click()
    expect(dialog).to_have_count(0)
    assert key_names(page) == ["id_ed25519", "id_rsa"]
    patch = [
        c for c in calls(page) if c["method"] == "PATCH" and c["path"].startswith("/api/keys/")
    ][-1]
    assert patch["body"] == {"hidden": True, "reassign": {"auth_method": "password"}}
    page.get_by_role("button", name="Show hidden keys (1)").click()
    page.get_by_role("button", name="Unhide id_ed25519_portkey").click()
    expect(page.locator(".table--keys .name-btn")).to_have_count(3)
    page.keyboard.press("Alt+0")  # Home catches up when it is shown again
    expect(page.locator(".table--connections tbody tr", has_text="imaging-01")).to_contain_text(
        "Password"
    )


def test_hide_an_unused_key_with_undo(app: AppFactory) -> None:
    page = app()
    open_keys(page)
    page.locator(".table--keys .name-btn", has_text="id_rsa").click()
    page.locator(".key-detail").get_by_role("button", name="Hide from Port-Key").click()
    expect_toast(page, "Hid id_rsa from Port-Key.")
    assert key_names(page) == ["id_ed25519", "id_ed25519_portkey"]
    page.get_by_role("button", name="Undo").click()
    expect(page.locator(".table--keys .name-btn")).to_have_count(3)


def test_settings_save_on_change(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    font = page.get_by_label("Terminal font size")
    font.fill("40")
    font.press("Tab")
    expect(page.locator("#st-font-error")).to_have_text("Use a size from 8 to 32.")
    font.fill("16")
    font.press("Tab")
    expect(page.locator(".save-status").first).to_have_text("Saved")
    page.get_by_label("Warn before leaving with connected sessions").uncheck()
    expect(page.locator(".settings").get_by_text("Saved")).to_have_count(2)
    page.get_by_label("Default key").select_option(label="id_rsa")
    expect(page.locator(".settings").get_by_text("Saved")).to_have_count(3)
    bodies = [
        c["body"] for c in calls(page) if c["method"] == "PATCH" and c["path"] == "/api/settings"
    ]
    assert {"font_size": 16} in bodies
    assert {"leave_warning": False} in bodies
    assert any(b.get("default_key_fp", "").startswith("SHA256:b7Lw") for b in bodies)
    expect(page.locator(".about")).to_contain_text("C:\\Users\\john\\.ssh\\port-key")


def test_change_password(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_label("Current password").fill("not it")
    page.get_by_label("New password").fill("a new password")
    page.get_by_label("Confirm").fill("a new password")
    page.get_by_role("button", name="Change password").click()
    expect(page.locator("#st-old-error")).to_have_text("That password is not correct. Try again.")
    expect(page.locator(".login")).to_have_count(0)  # a wrong password is not a sign-out
    page.get_by_label("Current password").fill(PASSWORD)
    page.get_by_role("button", name="Change password").click()
    expect_toast(page, "Password changed. Other browsers are signed out.")
    expect(page.get_by_label("New password")).to_have_value("")


def test_forget_host_key_needs_the_typed_host(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.locator(".hostkeys tbody tr")).to_have_count(3)
    expect(page.locator(".hostkeys tbody tr").first).to_contain_text("2 March 2026")
    page.get_by_role("button", name="Forget host key for 192.168.10.31:2222").click()
    dialog = page.get_by_role("dialog")
    forget = dialog.get_by_role("button", name="Forget host key")
    dialog.get_by_label("Type 192.168.10.31 to confirm").fill("192.168.10.3")
    expect(forget).to_be_disabled()
    dialog.get_by_label("Type 192.168.10.31 to confirm").fill("192.168.10.31")
    expect(forget).to_be_enabled()
    forget.click()
    expect_toast(page, "Forgot the host key for 192.168.10.31.")
    expect(page.locator(".hostkeys tbody tr")).to_have_count(2)


def test_diagnostics(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Open Diagnostics").click()
    expect(page.get_by_role("tab", name="Diagnostics")).to_have_attribute("aria-selected", "true")
    report = page.locator(".diag__report")
    expect(report).to_contain_text("C:\\Users\\john\\.ssh ✓ found")
    expect(report).to_contain_text(
        "C:\\Users\\john\\.ssh\\work.ppk — PuTTY format, not supported"
    )
    expect(report).to_contain_text("Port-Key 0.1.0 · Python 3.12.8 · asyncssh 2.18.0")
    page.get_by_label("Host name or address").fill("build-box")
    page.get_by_role("button", name="Check", exact=True).click()
    result = page.locator(".diag__result")
    expect(result).to_contain_text("build-box resolves to 192.168.10.20.")
    expect(result).to_contain_text("Port 22 answered in 4 ms.")
    page.get_by_label("Host name or address").fill("vpn-gw")
    page.get_by_role("button", name="Check", exact=True).click()
    expect(result).to_contain_text("Port 22 did not answer: it timed out.")
    page.get_by_label("Host name or address").fill("nas-99")
    page.get_by_role("button", name="Check", exact=True).click()
    expect(result).to_contain_text("Couldn't resolve nas-99: Name or service not known.")
    page.get_by_label("Host name or address").fill("john@nas")
    page.get_by_role("button", name="Check", exact=True).click()
    expect(page.locator("#dg-host-error")).to_contain_text("without spaces, @ or /")


def test_settings_keep_typing_when_the_key_list_changes(app: AppFactory) -> None:
    # Against the real backend the key scan can finish after Settings opened; that used to
    # redraw the whole page and wipe what was typed and the "Saved" note.
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    font = page.get_by_label("Terminal font size")
    font.fill("17")
    font.press("Tab")
    expect(page.locator(".save-status").first).to_have_text("Saved")
    page.get_by_label("Current password").fill("half typed")
    options = page.locator("#st-key option")
    expect(options).to_have_count(4)  # Automatic + 3 keys
    page.evaluate(
        "import('/js/store.js').then((s) => s.setState({keys: s.getState().keys.slice(1)}))"
    )
    expect(options).to_have_count(3)
    expect(page.get_by_label("Current password")).to_have_value("half typed")
    expect(page.locator(".save-status").first).to_have_text("Saved")
