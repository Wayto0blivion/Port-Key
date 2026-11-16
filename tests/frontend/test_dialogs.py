"""The modal primitive, Edit connection, the tag field and the folder picker (package 8a)."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.frontend.conftest import AppFactory, calls, expect_toast


def edit(page: Page, name: str) -> None:
    page.get_by_role("button", name=f"{name} menu").last.click()
    page.get_by_role("menuitem", name="Edit").click()
    expect(page.get_by_role("dialog", name=f"Edit {name}")).to_be_visible()


def test_edit_saves_only_changed_fields(app: AppFactory) -> None:
    page = app()
    edit(page, "web-prod")
    expect(page.locator("#ec-name")).to_be_focused()
    expect(page.locator("#ec-folder")).to_have_value(
        page.locator("#ec-folder option", has_text="Office LAN / Servers").get_attribute("value")
        or ""
    )
    expect(page.locator("input[name=ec-color][value=red]")).to_be_checked()
    page.locator("#ec-notes").fill("Production. Still red.")
    page.locator("#ec-tags").fill("Web,")
    expect(page.locator(".tagfield .chip__text")).to_have_text(
        ["#docker", "#linux", "#prod", "#web"]
    )
    page.get_by_label("Blue").check()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect_toast(page, "Saved changes to web-prod.")
    patch = [c for c in calls(page) if c["method"] == "PATCH"][-1]
    assert patch["body"] == {
        "tags": ["docker", "linux", "prod", "web"],
        "color": "blue",
        "notes": "Production. Still red.",
    }
    expect(page.get_by_role("button", name="web-prod menu").last).to_be_focused()  # focus returns


def test_edit_validation_and_server_errors(app: AppFactory) -> None:
    page = app()
    edit(page, "web-prod")
    page.locator("#ec-host").fill("john@192.168.10.42")
    page.locator("#ec-port").fill("70000")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#ec-host-error")).to_have_text(
        "A host has no spaces, @ or /. Put the user name in User."
    )
    expect(page.locator("#ec-port-error")).to_have_text("Use a port from 1 to 65535.")
    expect(page.locator("#ec-host")).to_be_focused()
    expect(page.locator("#ec-host")).to_have_attribute("aria-invalid", "true")
    page.locator("#ec-host").fill("192.168.10.42")
    page.locator("#ec-port").fill("22")
    page.locator("#ec-name").fill("BUILD-BOX")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#ec-name-error")).to_have_text(
        "A connection named build-box already exists. Choose another name."
    )
    page.locator("#ec-name").fill("web-prod")
    page.evaluate("window.__pkMock.failNext('/api/connections/', 422, 'jump_too_deep', 'too deep')")
    page.locator("#ec-jump").select_option(label="vpn-gw")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#ec-jump-error")).to_have_text("Jump chains can be at most 3 hosts long.")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_jump_via_excludes_self_and_loops(app: AppFactory) -> None:
    page = app()
    edit(page, "build-box")
    options = page.locator("#ec-jump option").all_text_contents()
    assert options[0] == "None, connect directly"
    assert "build-box" not in options
    assert "parser-vm" not in options  # parser-vm jumps through build-box
    assert "vpn-gw" in options
    expect(page.locator("#ec-auth option")).to_have_text(
        ["Default key (id_ed25519)", "id_ed25519", "id_rsa", "id_ed25519_portkey", "Password only"]
    )


def test_tag_field_keyboard_and_rules(app: AppFactory) -> None:
    page = app()
    edit(page, "vpn-gw")
    tags = page.locator("#ec-tags")
    tags.fill("ne")
    listbox = page.get_by_role("listbox", name="Existing tags")
    expect(listbox).to_be_hidden()  # "network" is already on vpn-gw
    tags.fill("li")
    expect(listbox.get_by_role("option")).to_have_text(["#linux", "#appliance"])  # prefix first
    tags.press("ArrowDown")
    expect(tags).to_have_attribute("aria-activedescendant", "ec-tags-list-0")
    tags.press("Enter")
    expect(page.locator(".tagfield .chip__text")).to_have_text(["#linux", "#network"])
    tags.fill("#Lab ok! ")
    expect(page.locator(".tagfield .chip__text")).to_have_text(["#lab", "#linux", "#network"])
    expect(page.locator("#ec-tags-tagerror")).to_contain_text('"ok!" is not a valid tag.')
    tags.fill("")
    tags.press("Backspace")
    expect(page.locator(".tagfield .chip__text")).to_have_text(["#lab", "#linux"])
    page.get_by_role("button", name="Remove tag lab").click()
    expect(page.locator(".tagfield .chip__text")).to_have_text(["#linux"])
    tags.press("Escape")  # nothing open in the list: Esc closes the dialog
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_folder_picker_new_folder(app: AppFactory) -> None:
    page = app()
    edit(page, "vpn-gw")
    page.locator("#ec-folder").select_option(label="New folder…")
    new = page.get_by_role("group", name="New folder")
    expect(new.get_by_label("New folder name")).to_be_focused()
    new.get_by_label("Inside").select_option(label="Office LAN")
    new.get_by_label("New folder name").fill("Lab")
    new.get_by_label("New folder name").press("Enter")  # creates, does not submit the dialog
    expect(page.get_by_role("dialog", name="Edit vpn-gw")).to_be_visible()
    expect(page.locator("#ec-folder option:checked")).to_have_text("Office LAN / Lab")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".table--connections tbody tr", has_text="vpn-gw")).to_contain_text(
        "Office LAN / Lab"
    )


def test_modal_traps_focus_and_restores_it(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Forget host key for 192.168.10.42").click()
    dialog = page.get_by_role("dialog", name="Forget the host key for 192.168.10.42?")
    cancel = dialog.get_by_role("button", name="Cancel")
    expect(cancel).to_be_focused()  # danger dialog: the safe button first
    forget = dialog.get_by_role("button", name="Forget host key")
    expect(forget).to_be_disabled()
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('dialog') !== null")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("button", name="Forget host key for 192.168.10.42")).to_be_focused()


def test_duplicate_opens_the_copy(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="web-prod menu").last.click()
    page.get_by_role("menuitem", name="Duplicate").click()
    expect(page.get_by_role("dialog", name="Edit web-prod (copy)")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".table--connections")).to_contain_text("web-prod (copy)")
