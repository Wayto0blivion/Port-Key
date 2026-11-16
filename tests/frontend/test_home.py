"""Home: Recent cards, the table, bulk bar, reachability, suggestions and first run (package 8a)."""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

from tests.frontend.conftest import AppFactory, calls, expect_toast


def table_row(page: Page, name: str) -> Locator:
    return page.locator(".table--connections tbody tr").filter(
        has=page.get_by_role("button", name=name, exact=True)
    )


def table_names(page: Page) -> list[str]:
    names: list[str] = page.locator(".table--connections .name-btn").all_text_contents()
    return names


def test_recent_cards_and_sorting(app: AppFactory) -> None:
    page = app()
    cards = page.locator(".cards .card__name")
    expect(cards).to_have_count(6)
    assert cards.first.text_content() == "web-prod"  # most recent first
    assert (
        page.locator(".card")
        .first.locator(".card__last")
        .text_content()
        .startswith("Last connected ")
    )
    assert table_names(page) == [
        "backup-nas",
        "build-box",
        "imaging-01",
        "parser-vm",
        "print-srv",
        "vpn-gw",
        "web-prod",
    ]
    name_th = page.locator("th").filter(has_text="Name")
    expect(name_th).to_have_attribute("aria-sort", "ascending")
    page.get_by_role("button", name="Name", exact=True).click()
    expect(name_th).to_have_attribute("aria-sort", "descending")
    assert table_names(page)[0] == "web-prod"
    page.get_by_role("button", name="Last connected", exact=True).click()
    assert table_names(page)[:2] == ["web-prod", "imaging-01"]
    expect(table_row(page, "print-srv")).to_contain_text("Password")
    expect(table_row(page, "imaging-01")).to_contain_text("id_ed25519_portkey")
    expect(table_row(page, "web-prod")).to_contain_text("Office LAN / Servers")
    expect(table_row(page, "web-prod").locator(".chip")).to_have_text(
        ["#docker", "#linux", "#prod"]
    )


def test_reachability_dots(app: AppFactory) -> None:
    page = app()
    expect(table_row(page, "web-prod").locator(".dot")).to_have_class("dot dot--up")
    vpn = table_row(page, "vpn-gw").locator(".dot")
    expect(vpn).to_have_class("dot dot--down")
    expect(vpn).to_have_attribute("title", "No response on port 22")
    parser = table_row(page, "parser-vm").locator(".dot")
    expect(parser).to_have_class("dot dot--skipped")
    expect(parser).to_have_attribute("title", "Checked when you connect, reached via build-box")
    assert "reachable" in table_row(page, "web-prod").locator(".dot").text_content()
    reach = [c for c in calls(page) if c["path"] == "/api/connections/reachability"]
    assert len(reach) == 1
    assert len(reach[0]["body"]["ids"]) == 6  # parser-vm goes through a jump host


def test_bulk_add_tag_with_undo(app: AppFactory) -> None:
    page = app()
    page.get_by_label("Select parser-vm").check()
    page.get_by_label("Select imaging-01").check()
    bar = page.get_by_role("toolbar", name="Selected connections")
    expect(bar).to_contain_text("2 selected")
    bar.get_by_role("button", name="Add tag…").click()
    page.locator("#bulk-tags").fill("dell")
    page.locator("#bulk-tags").press("Enter")
    page.get_by_role("button", name="Add tags").click()
    expect_toast(page, "Added #dell to 2 connections.")
    expect(table_row(page, "parser-vm").locator(".chip")).to_contain_text(["#dell"])
    bulk = [c for c in calls(page) if c["path"] == "/api/connections/bulk"][-1]
    assert bulk["body"]["add_tags"] == ["dell"]
    assert len(bulk["body"]["ids"]) == 2
    page.get_by_role("button", name="Undo").click()
    expect(table_row(page, "parser-vm").locator(".chip")).to_have_text(["#appliance", "#linux"])


def test_bulk_move_and_remove_tag(app: AppFactory) -> None:
    page = app()
    page.get_by_label("Select parser-vm").check()
    page.get_by_label("Select vpn-gw").check()
    page.get_by_role("button", name="Move to folder…").click()
    page.locator("#bulk-folder").select_option(label="Office LAN / Servers")
    page.get_by_role("button", name="Move", exact=True).click()
    expect_toast(page, "Moved 2 connections to Office LAN / Servers.")
    expect(table_row(page, "vpn-gw")).to_contain_text("Office LAN / Servers")
    page.get_by_role("button", name="Remove tag…").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("on 1 of 2")
    dialog.get_by_label("#network on 1 of 2").check()
    dialog.get_by_role("button", name="Remove tags").click()
    expect(table_row(page, "vpn-gw").locator(".chip")).to_have_count(0)
    page.get_by_role("button", name="Undo").last.click()
    expect(table_row(page, "vpn-gw").locator(".chip")).to_have_text(["#network"])


def test_select_all_range_and_clear(app: AppFactory) -> None:
    page = app()
    page.get_by_label("Select all connections shown").check()
    expect(page.locator(".bulkbar__count")).to_have_text("7 selected")
    page.get_by_role("button", name="Clear selection").click()
    expect(page.locator(".bulkbar")).to_have_count(0)
    expect(page.get_by_label("Select all connections shown")).to_be_focused()
    page.get_by_label("Select backup-nas").check()
    page.get_by_label("Select parser-vm").click(modifiers=["Shift"])
    expect(page.locator(".bulkbar__count")).to_have_text("4 selected")
    assert page.evaluate(
        "document.querySelector('[aria-label=\"Select all connections shown\"]').indeterminate"
    )


def test_sidebar_filter_narrows_the_table(app: AppFactory) -> None:
    page = app()
    page.locator("#sidebar-filter").fill("#linux")
    expect(page.locator(".table--connections .name-btn")).to_have_count(4)
    expect(page.locator(".section-head")).to_contain_text(["4 of 7 shown, filtered in the sidebar"])
    page.locator(".section-head").get_by_role("button", name="Clear filter").click()
    expect(page.locator(".table--connections .name-btn")).to_have_count(7)


def test_home_suggestions_dismiss_with_undo(app: AppFactory) -> None:
    page = app()
    panel = page.locator(".suggest-panel")
    expect(panel.locator(".suggestion")).to_have_count(3)
    expect(panel.locator(".suggestion").first).to_contain_text("lab-01 · 192.168.10.71")
    expect(panel.locator(".suggestion").nth(1)).to_contain_text("192.168.10.88:2200")
    panel.get_by_role("button", name="Dismiss lab-01 · 192.168.10.71").click()
    expect_toast(page, "Dismissed lab-01.")
    expect(panel.locator(".suggestion").first).not_to_contain_text("lab-01")
    page.get_by_role("button", name="Undo").click()
    expect(panel.locator(".suggestion").first).to_contain_text("lab-01")
    expect(page.locator(".home")).to_contain_text("6 hashed entries can't be listed.")


def test_first_run_with_a_key(app: AppFactory) -> None:
    page = app("empty")
    home = page.locator(".home")
    expect(home.get_by_role("heading", name="No connections yet")).to_be_visible()
    expect(home).to_contain_text("Port-Key found your key id_ed25519 in C:\\Users\\john\\.ssh.")
    expect(home.locator(".fp-box")).to_have_text("ED25519 SHA256:Xk3fP0…r9Qa")
    expect(home.locator(".suggestion")).to_have_count(3)
    home.get_by_role("button", name="See all 14").click()
    expect(home.locator(".suggestion")).to_have_count(14)
    home.get_by_role("button", name="Copy public key").click()
    expect_toast(page, "Public key copied")
    assert page.evaluate("navigator.clipboard.readText()").startswith("ssh-ed25519 AAAA")
    expect(page.locator("#sidebar-filter")).to_be_disabled()
    expect(page.locator(".sidebar")).to_contain_text(
        "Saved connections appear here, grouped the way you choose."
    )


def test_first_run_without_a_key(app: AppFactory) -> None:
    page = app("nokey")
    home = page.locator(".home")
    expect(home.get_by_role("heading", name="No SSH key found")).to_be_visible()
    expect(home).to_contain_text(
        "Port-Key looked in C:\\Users\\john\\.ssh and found no key pair."
    )
    home.get_by_role("button", name="Check again").click()
    expect_toast(page, "Still no key pair in C:\\Users\\john\\.ssh.")
    home.get_by_role("button", name="Add connection with a password").click()
    dialog = page.get_by_role("dialog", name="Add connection")
    expect(page.locator("#ec-auth")).to_have_value("password")
    expect(page.locator("#ec-user")).to_have_value("john")
    page.locator("#ec-host").fill("192.168.10.15")
    page.locator("#ec-name").fill("print-srv")
    dialog.get_by_role("button", name="Save connection").click()
    expect(page.locator(".table--connections")).to_contain_text("print-srv")
    expect(table_row(page, "print-srv")).to_contain_text("Password")


def test_first_run_with_only_a_putty_key(app: AppFactory) -> None:
    page = app("ppk")
    expect(page.locator(".home")).to_contain_text(
        "Port-Key found a PuTTY key (.ppk), which it can't use."
        " Generate a key here and install it with the wizard."
    )
    page.locator(".home").get_by_role("button", name="Generate key").click()
    expect(page.get_by_role("dialog", name="Generate key")).to_be_visible()


def test_load_error_and_retry(app: AppFactory) -> None:
    page = app("error")
    expect(page.locator(".home")).to_contain_text("Couldn't load your connections")
    expect(page.locator(".sidebar")).to_contain_text(
        "Couldn't load connections. Something went wrong inside Port-Key."
    )


def test_row_menu_stays_open_when_opening_it_scrolls_the_table(app: AppFactory) -> None:
    # At 1280 px the table is wider than its pane, so the "⋯" button is half hidden; revealing
    # it scrolls the table after the menu has opened (found against the real backend).
    page = app(width=1280, height=800)
    wrap_scroll = "document.querySelector('.table-wrap').scrollLeft"
    assert page.evaluate(wrap_scroll) == 0
    table_row(page, "web-prod").get_by_role("button", name="web-prod menu").click()
    page.wait_for_timeout(300)
    assert page.evaluate(wrap_scroll) > 0
    menu = page.get_by_role("menu")
    expect(menu).to_be_visible()
    menu.get_by_role("menuitem", name="Edit").click()
    expect(page.get_by_role("dialog", name="Edit web-prod")).to_be_visible()
