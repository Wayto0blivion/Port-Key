"""The sidebar: folder tree, filter and tag chips, keyboard, menus, drag and drop (package 8a)."""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

from tests.frontend.conftest import AppFactory, calls, expect_toast


def folder(page: Page, name: str) -> Locator:
    head = page.locator(f".tree-folder__head:has(> .tree-folder__name:text-is('{name}'))")
    return head.locator("xpath=..")


def row(page: Page, name: str, nth: int = -1) -> Locator:
    rows = page.locator(".tree .tree-conn").filter(
        has=page.locator(f".conn-row__name:text-is('{name}')")
    )
    return rows.last if nth == -1 else rows.nth(nth)


def visible_rows(page: Page) -> list[str]:
    names: list[str] = page.locator(".tree .conn-row__name").all_text_contents()
    return names


def test_tree_structure(app: AppFactory) -> None:
    page = app()
    heads = page.locator(".tree .tree-folder__name").all_text_contents()
    assert heads == ["Pinned", "Appliances", "Office LAN", "Servers", "VPN", "Ungrouped"]
    expect(folder(page, "Servers")).to_have_attribute("aria-level", "2")
    expect(
        folder(page, "Office LAN").locator("> .tree-folder__head .tree-folder__count")
    ).to_contain_text("4")
    # The count's screen-reader word is singular for one (found end to end, package 9).
    expect(folder(page, "VPN").locator("> .tree-folder__head .tree-folder__count")).to_have_text(
        "1 connection"
    )
    pinned = folder(page, "Pinned").locator(".conn-row__name").all_text_contents()
    assert pinned == ["build-box", "web-prod"]
    expect(row(page, "print-srv").locator(".pw-glyph")).to_have_text("pw")
    expect(row(page, "web-prod").locator(".badge")).to_have_count(0)
    page.evaluate(
        "import('/js/store.js').then((s) => s.setSessionInfo("
        "{counts: {[s.getState().connections[0].id]: 2}, connected: 2}))"
    )
    expect(row(page, "build-box", 0).locator(".badge")).to_contain_text("2")


def test_filter_text_tags_and_chips(app: AppFactory) -> None:
    page = app()
    flt = page.locator("#sidebar-filter")
    flt.fill("appl")  # matches the folder path "Appliances"
    assert sorted(visible_rows(page)) == ["imaging-01", "parser-vm"]
    assert page.locator(".tree .tree-folder__name").all_text_contents() == ["Appliances"]
    flt.fill("#prod")
    assert visible_rows(page) == ["web-prod", "web-prod"]  # Pinned and Office LAN / Servers
    flt.fill("")
    chip = page.get_by_role("button", name="#linux", exact=True)
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "true")
    flt.fill("build")
    assert visible_rows(page) == ["build-box", "build-box"]
    flt.press("Escape")
    expect(flt).to_have_value("")
    expect(page.get_by_role("button", name="#linux", exact=True)).to_have_attribute(
        "aria-pressed", "false"
    )
    assert len(visible_rows(page)) == 9


def test_more_chips_and_no_match(app: AppFactory) -> None:
    page = app()
    more = page.get_by_role("button", name="More…")
    expect(page.locator(".tagchips .chip--filter")).to_have_count(6)
    more.click()
    expect(page.locator(".tagchips .chip--filter")).to_have_count(7)
    expect(page.get_by_role("button", name="Fewer")).to_be_focused()
    page.locator("#sidebar-filter").fill("zzz-nothing")
    expect(page.get_by_text("No connections match this filter.")).to_be_visible()
    page.get_by_role("button", name="Clear filter").first.click()
    expect(page.locator("#sidebar-filter")).to_have_value("")
    expect(page.locator("#sidebar-filter")).to_be_focused()


def test_keyboard_navigation_and_collapse_persists(app: AppFactory) -> None:
    page = app()
    page.locator("#sidebar-filter").press("ArrowDown")
    pinned = folder(page, "Pinned")
    expect(pinned).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(row(page, "build-box", 0)).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(pinned).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(pinned).to_have_attribute("aria-expanded", "false")
    expect(pinned).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(folder(page, "Appliances")).to_be_focused()
    page.keyboard.press("o")  # type-ahead: next item starting with "o"
    expect(folder(page, "Office LAN")).to_be_focused()
    page.keyboard.press("End")
    expect(folder(page, "Ungrouped")).to_be_focused()
    assert (
        page.evaluate("JSON.parse(localStorage.getItem('portkey.folders.collapsed')).__pinned")
        is True
    )
    page.reload()
    expect(folder(page, "Pinned")).to_have_attribute("aria-expanded", "false")
    folder(page, "Pinned").locator(".tree-folder__head").click()
    expect(folder(page, "Pinned")).to_have_attribute("aria-expanded", "true")


def test_row_menu_by_keyboard(app: AppFactory) -> None:
    page = app()
    target = row(page, "web-prod")
    target.focus()
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="web-prod")
    expect(menu).to_be_visible()
    items = menu.get_by_role("menuitem").all_text_contents()
    assert items == [
        "Connect",
        "New session",
        "Edit",
        "Duplicate",
        "Unpin",
        "Move to folder…",
        "Copy ssh command",
        "Delete",
    ]
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("ArrowUp")
    expect(menu.get_by_role("menuitem", name="Delete")).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(target).to_be_focused()


def test_copy_ssh_command(app: AppFactory) -> None:
    page = app()
    row(page, "imaging-01").click(button="right")
    page.get_by_role("menuitem", name="Copy ssh command").click()
    expect_toast(page, "ssh command copied")
    assert page.evaluate("navigator.clipboard.readText()") == "ssh -p 2222 admin@192.168.10.31"


def test_delete_with_undo(app: AppFactory) -> None:
    page = app()
    row(page, "vpn-gw").click(button="right")
    page.get_by_role("menuitem", name="Delete").click()
    expect(page.locator(".tree").get_by_text("vpn-gw", exact=True)).to_have_count(0)
    expect_toast(page, "Deleted vpn-gw.")
    page.get_by_role("button", name="Undo").click()
    expect(page.locator(".tree").get_by_text("vpn-gw", exact=True)).to_have_count(1)


def test_undo_delete_restores_the_same_connection(app: AppFactory) -> None:
    # Review finding: Undo created a new connection (new id, no usage), so open tabs stayed
    # "(deleted)", Reconnect failed and the host dropped out of Recent.
    page = app()
    row(page, "vpn-gw").click(button="right")
    page.get_by_role("menuitem", name="Delete").click()
    expect_toast(page, "Deleted vpn-gw.")
    page.get_by_role("button", name="Undo").click()
    expect_toast(page, "Restored vpn-gw.")
    [deleted] = [c["path"] for c in calls(page) if c["method"] == "DELETE"]
    [restored] = [c["path"] for c in calls(page) if c["path"].endswith("/restore")]
    assert restored == f"{deleted}/restore"  # the same id comes back
    assert not [c for c in calls(page) if c["method"] == "POST" and c["path"] == "/api/connections"]


def test_delete_jump_host_is_blocked(app: AppFactory) -> None:
    page = app()
    row(page, "build-box").click(button="right")
    page.get_by_role("menuitem", name="Delete").click()
    dialog = page.get_by_role("dialog", name="Can't delete build-box")
    expect(dialog).to_contain_text(
        "build-box is the jump host for 1 connection: parser-vm."
        " Change their Jump via setting first."
    )
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)


def test_folder_menu_new_and_delete_with_undo(app: AppFactory) -> None:
    page = app()
    folder(page, "Office LAN").locator(".tree-folder__head").first.click(button="right")
    page.get_by_role("menuitem", name="New folder").click()
    page.get_by_label("Folder name").fill("Printers")
    page.keyboard.press("Enter")
    expect(folder(page, "Printers")).to_have_attribute("aria-level", "2")
    folder(page, "Servers").locator(".tree-folder__head").click(button="right")
    page.get_by_role("menuitem", name="Delete").click()
    expect_toast(page, "Deleted folder Servers. Its contents moved to Office LAN.")
    expect(folder(page, "Servers")).to_have_count(0)
    assert "backup-nas" in folder(page, "Office LAN").locator(".conn-row__name").all_text_contents()
    page.get_by_role("button", name="Undo").click()
    expect(folder(page, "Servers")).to_have_count(1)
    assert sorted(folder(page, "Servers").locator(".conn-row__name").all_text_contents()) == [
        "backup-nas",
        "build-box",
        "web-prod",
    ]


def test_drag_connection_onto_folder(app: AppFactory) -> None:
    page = app()
    row(page, "vpn-gw").drag_to(folder(page, "Appliances").locator(".tree-folder__head"))
    expect_toast(page, "Moved vpn-gw to Appliances.")
    assert "vpn-gw" in folder(page, "Appliances").locator(".conn-row__name").all_text_contents()
    patch = [c for c in calls(page) if c["method"] == "PATCH"][-1]
    assert list(patch["body"]) == ["group_id"]


def test_many_connections_stay_fast(app: AppFactory) -> None:
    page = app("many")
    expect(page.locator(".tree .tree-conn")).to_have_count(326)  # 320 + 6 pinned copies
    elapsed = page.evaluate("""async () => {
      const input = document.getElementById('sidebar-filter');
      const times = [];
      for (const text of ['s', 'sr', 'srv-1', 'srv-12', '#dell', '']) {
        const t0 = performance.now();
        input.value = text;
        input.dispatchEvent(new Event('input'));
        await new Promise((r) => requestAnimationFrame(() => r()));
        times.push(performance.now() - t0);
      }
      return Math.max(...times);
    }""")
    assert elapsed < 250, f"slowest filter update took {elapsed:.0f} ms"
    page.locator("#sidebar-filter").fill("srv-12")
    assert sorted(set(visible_rows(page))) == [f"srv-12{n}" for n in range(10)]
