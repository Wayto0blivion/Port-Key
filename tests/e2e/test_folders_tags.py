"""Folders and tags in the browser against the real API (docs/ux-spec.md "Folder actions",
"Bulk bar"): create a folder and a nested one, move a connection by dragging it onto a folder,
tag several connections with the bulk bar, and filter by tag. Each run starts by removing what
the last one made."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Locator, Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import Api, open_app, sidebar_row, toast
from tests.fixtures import testbed as tb

TOP, CHILD, TAG = "FT Office", "FT Servers", "ft-batch"
NAMES = ("ft-alpha", "ft-bravo", "ft-charlie")


def folder(page: Page, name: str) -> Locator:
    head = page.locator(f".tree-folder__head:has(> .tree-folder__name:text-is('{name}'))")
    return head.locator("xpath=..")


def table_row(page: Page, name: str) -> Locator:
    return page.locator(".table--connections tbody tr").filter(
        has=page.get_by_role("button", name=name, exact=True)
    )


def reset(api: Api) -> dict[str, dict[str, Any]]:
    groups = {g["name"]: g for g in api.call("GET", "/api/groups")["groups"]}
    for name in (CHILD, TOP):  # the child first, so nothing moves up into the root
        if name in groups:
            api.call("DELETE", f"/api/groups/{groups[name]['id']}")
    conns = {}
    for name in NAMES:
        c = api.ensure(name, host=tb.SSHD.host, username="keyonly")
        conns[name] = api.call(
            "PATCH", f"/api/connections/{c['id']}", {"group_id": None, "tags": []}
        )
    return conns


def test_nested_folder_move_bulk_tag_and_filter(
    page: Page, base_url: str, app_api: AppApi, app_token: str
) -> None:
    api = Api(app_api, app_token)
    conns = reset(api)
    watch = open_app(page, base_url, app_token)

    # A top-level folder from Ungrouped's menu, then one inside it from its own menu.
    folder(page, "Ungrouped").locator(".tree-folder__head").first.click(button="right")
    page.get_by_role("menuitem", name="New folder").click()
    page.get_by_label("Folder name").fill(TOP)
    page.keyboard.press("Enter")
    expect(folder(page, TOP)).to_have_attribute("aria-level", "1")
    folder(page, TOP).locator(".tree-folder__head").first.click(button="right")
    page.get_by_role("menuitem", name="New folder").click()
    page.get_by_label("Folder name").fill(CHILD)
    page.keyboard.press("Enter")
    expect(folder(page, CHILD)).to_have_attribute("aria-level", "2")

    # Drag a connection onto the nested folder.
    sidebar_row(page, "ft-alpha").first.drag_to(folder(page, CHILD).locator(".tree-folder__head"))
    toast(page, f"Moved ft-alpha to {TOP} / {CHILD}.")
    expect(table_row(page, "ft-alpha")).to_contain_text(f"{TOP} / {CHILD}")
    expect(folder(page, TOP).locator("> .tree-folder__head .tree-folder__count")).to_have_text(
        "1 connection"  # the number, and the word for screen readers
    )

    # Tag all three at once with the bulk bar.
    for name in NAMES:
        page.get_by_label(f"Select {name}").check()
    bar = page.get_by_role("toolbar", name="Selected connections")
    expect(bar).to_contain_text("3 selected")
    bar.get_by_role("button", name="Add tag…").click()
    page.locator("#bulk-tags").fill("#FT-Batch")
    page.locator("#bulk-tags").press("Enter")
    page.get_by_role("button", name="Add tags").click()
    toast(page, f"Added #{TAG} to 3 connections.")
    for name in NAMES:
        expect(table_row(page, name).locator(".chip")).to_have_text([f"#{TAG}"])
    page.get_by_role("button", name="Clear selection").click()

    # Filter by the tag: its chip in the sidebar narrows the tree and the Home table.
    chip = page.locator(".tagchips").get_by_role("button", name=f"#{TAG}", exact=True)
    if not chip.is_visible():
        page.get_by_role("button", name="More…").click()
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".table--connections .name-btn")).to_have_text(list(NAMES))
    expect(page.locator(".tree .conn-row__name")).to_have_text(list(NAMES))
    chip.click()
    page.locator("#sidebar-filter").fill(f"#{TAG} bravo")
    expect(page.locator(".table--connections .name-btn")).to_have_text(["ft-bravo"])
    page.locator("#sidebar-filter").fill("")

    # What the server stored.
    groups = {g["name"]: g for g in api.call("GET", "/api/groups")["groups"]}
    assert groups[CHILD]["parent_id"] == groups[TOP]["id"]
    assert groups[TOP]["connection_count"] == 1  # counts include descendants
    now = api.connections()
    assert now["ft-alpha"]["group_id"] == groups[CHILD]["id"]
    assert [now[n]["tags"] for n in NAMES] == [[TAG]] * 3
    assert {n: now[n]["id"] for n in NAMES} == {n: conns[n]["id"] for n in NAMES}
    assert {"name": TAG, "count": 3} in api.call("GET", "/api/tags")["tags"]
    watch.check()
