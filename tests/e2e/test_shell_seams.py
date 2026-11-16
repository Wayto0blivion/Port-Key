"""The frontend shell against the real backend (packages 1, 2, 3 and 7 behind 8a's views).

Written at the phase A integration to check the seams the mock API cannot: auth headers, error
envelopes and field names as the real server sends them. Package 9 owns it now and may fold it
into its own specs. Routes of packages 4 to 6 still answer 501; the shell must not throw there.

Every page records page errors, console errors and every /api response of 400 or more; each
test checks them against what it expects. Chromium logs a failed HTTP status to the console as
"Failed to load resource"; those lines are judged by the response list instead.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, Response, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.harness.browser import sign_in_ui, sign_in_with_token, stored_token
from tests.fixtures import testbed as tb

FOLDER = "Seam lab"
DIRECT, ALT_PORT, INNER = "seam-direct", "seam-alt-port", "seam-inner"


class Watch:
    def __init__(self, page: Page) -> None:
        self.errors: list[str] = []
        self.failures: list[tuple[int, str, str]] = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", self._console)
        page.on("response", self._response)

    def _console(self, msg: Any) -> None:
        if msg.type != "error" or msg.text.startswith("Failed to load resource"):
            return
        # The app sends Cross-Origin-Opener-Policy. Chromium ignores it, with this console
        # error, on an origin it does not consider trustworthy, such as http://app:8000 in this
        # test bed. The owner's http://localhost:8822 is trustworthy, so it does not appear there.
        if msg.text.startswith("The Cross-Origin-Opener-Policy header has been ignored"):
            return
        self.errors.append(f"console: {msg.text}")

    def _response(self, response: Response) -> None:
        path = response.url.split("://", 1)[-1].split("/", 1)[-1]
        if path.startswith("api/") and response.status >= 400:
            self.failures.append((response.status, response.request.method, "/" + path))

    def check(self, *allowed: tuple[int, str, str]) -> None:
        assert self.errors == []
        assert [f for f in self.failures if f not in allowed] == []


@pytest.fixture(scope="module")
def seeded(app_api: AppApi, app_token: str) -> dict[str, Any]:
    """A folder and three connections (direct, second port, behind a jump), made through the
    real API. Idempotent, so a rerun against a running stack works."""

    def call(method: str, path: str, body: Any = None) -> Any:
        r = app_api.request(method, path, token=app_token, json=body)
        assert r.status_code < 300, (method, path, r.status_code, r.text)
        return r.json() if r.content else None

    groups = {g["name"]: g for g in call("GET", "/api/groups")["groups"]}
    folder = groups.get(FOLDER) or call("POST", "/api/groups", {"name": FOLDER})
    have = {c["name"]: c for c in call("GET", "/api/connections")["connections"]}

    def ensure(name: str, **fields: Any) -> dict[str, Any]:
        if name in have:
            return have[name]
        created: dict[str, Any] = call("POST", "/api/connections", {"name": name, **fields})
        return created

    direct = ensure(
        DIRECT,
        host=tb.SSHD.host,
        port=tb.SSHD.port,
        username="keyonly",
        auth_method="key",
        group_id=folder["id"],
        tags=["#Seam", "linux"],
    )
    alt = ensure(
        ALT_PORT,
        host=tb.SSHD_ALT_PORT.host,
        port=tb.SSHD_ALT_PORT.port,
        username="pw",
        auth_method="password",
        tags=["seam"],
    )
    inner = ensure(
        INNER, host=tb.SSHD_INNER.host, username="keyonly", auth_method="key", jump_id=direct["id"]
    )
    return {"folder": folder, DIRECT: direct, ALT_PORT: alt, INNER: inner, "call": call}


def home_row(page: Page, name: str) -> Any:
    return page.locator(".table--connections tbody tr").filter(
        has=page.get_by_role("button", name=name, exact=True)
    )


def test_sign_in_through_the_ui(page: Page, base_url: str, app_api: AppApi, app_token: str) -> None:
    watch = Watch(page)
    page.goto(base_url + "/")
    expect(page.locator("#login-title")).to_have_text("Sign in to Port-Key")
    page.get_by_label("Password", exact=True).fill("not the password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator(".login__error")).to_have_text("That password is not correct. Try again.")
    sign_in_ui(page, tb.APP_PASSWORD)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    token = stored_token(page)
    assert token
    assert app_api.auth_status(token) == {"setup": True, "authenticated": True}
    watch.check((401, "POST", "/api/auth/login"))


def test_sidebar_and_home_over_the_real_api(
    page: Page, base_url: str, app_token: str, seeded: dict[str, Any]
) -> None:
    watch = Watch(page)
    sign_in_with_token(page, base_url, app_token)
    tree = page.locator(".tree")
    folder_head = tree.locator(".tree-folder__head").filter(has_text=FOLDER)
    expect(folder_head).to_be_visible()
    expect(tree.locator(".conn-row__name").filter(has_text=DIRECT).first).to_be_visible()
    expect(page.get_by_role("button", name="#seam", exact=True)).to_be_visible()

    # Home: the folder path and the normalised tags come back as the server stored them.
    row = home_row(page, DIRECT)
    expect(row).to_contain_text(FOLDER)
    expect(row.locator(".chip")).to_have_text(["#linux", "#seam"])
    expect(home_row(page, ALT_PORT)).to_contain_text("Password")
    # Reachability: a real TCP check from the app container; the jumped one is skipped.
    expect(row.locator(".dot")).to_have_class("dot dot--up", timeout=15_000)
    expect(home_row(page, ALT_PORT).locator(".dot")).to_have_class("dot dot--up")
    expect(home_row(page, INNER).locator(".dot")).to_have_class("dot dot--skipped")

    # Suggestions from the fixture known_hosts: sshd and [sshd]:2222 are saved, so left out.
    panel = page.locator(".suggest-panel")
    expect(panel.locator(".suggestion").first).to_be_visible()
    labels = " ".join(panel.locator(".suggestion__host").all_text_contents())
    assert "sshd" not in labels.split()
    assert "build-box" in labels or "web-prod" in labels

    # Filter by tag in the sidebar narrows the Home table.
    page.locator("#sidebar-filter").fill("#seam")
    expect(page.locator(".table--connections .name-btn")).to_have_text([ALT_PORT, DIRECT])
    page.locator("#sidebar-filter").fill("")
    watch.check()


def test_edit_and_bulk_tag_round_trip(
    page: Page, base_url: str, app_token: str, seeded: dict[str, Any]
) -> None:
    watch = Watch(page)
    sign_in_with_token(page, base_url, app_token)
    page.get_by_role("button", name=f"{ALT_PORT} menu").last.click()
    page.get_by_role("menuitem", name="Edit").click()
    page.fill("#ec-notes", "Second port of the test sshd")
    # A clash with another name is the server's 409 name_taken, shown on the Name field.
    page.fill("#ec-name", DIRECT.upper())
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#ec-name-error")).to_contain_text("already")
    page.fill("#ec-name", ALT_PORT)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("dialog.modal")).to_have_count(0)
    after = seeded["call"]("GET", "/api/connections")["connections"]
    assert next(c for c in after if c["name"] == ALT_PORT)["notes"] == (
        "Second port of the test sshd"
    )

    page.get_by_label(f"Select {DIRECT}").check()
    page.get_by_label(f"Select {ALT_PORT}").check()
    page.get_by_role("toolbar", name="Selected connections").get_by_role(
        "button", name="Add tag…"
    ).click()
    page.locator("#bulk-tags").fill("#Bulk-Seam")
    page.locator("#bulk-tags").press("Enter")
    page.get_by_role("button", name="Add tags").click()
    expect(home_row(page, ALT_PORT).locator(".chip")).to_contain_text(["#bulk-seam"])
    page.get_by_role("button", name="Undo").click()
    expect(home_row(page, ALT_PORT).locator(".chip")).to_have_text(["#seam"])
    watch.check((409, "PATCH", f"/api/connections/{seeded[ALT_PORT]['id']}"))


def test_keys_tab_over_the_real_api(page: Page, base_url: str, app_token: str) -> None:
    watch = Watch(page)
    sign_in_with_token(page, base_url, app_token)
    page.get_by_role("button", name="Keys", exact=True).click()
    names = page.locator(".table--keys .name-btn")
    expect(names.first).to_be_visible()
    shown = set(names.all_text_contents())
    assert {"id_ed25519", "id_rsa", "id_ecdsa", "id_ed25519_portkey"} <= shown
    detail = page.locator(".key-detail")
    page.locator(".table--keys .name-btn", has_text="id_ed25519").first.click()
    expect(detail).to_contain_text("C:\\Users\\john\\.ssh\\id_ed25519")
    expect(page.locator(".page")).to_contain_text("work.ppk")

    # The live name check asks the server, which only looks in the generated-keys folder.
    key_name = "id_seam_ed25519"
    dialog = page.get_by_role("dialog", name="Generate key")
    name = dialog.get_by_label("File name")
    if key_name not in shown:
        page.locator(".page__head").get_by_role("button", name="Generate key").click()
        name.fill(key_name)
        expect(dialog).to_contain_text("Name is free.")
        dialog.get_by_role("button", name="Generate key").click()
        expect(dialog).to_have_count(0)
        expect(page.locator(".key-detail .key-detail__name")).to_have_text(key_name)
        expect(page.locator(".key-detail")).to_contain_text(
            f"C:\\Users\\john\\.ssh\\port-key\\{key_name}"
        )
    page.locator(".page__head").get_by_role("button", name="Generate key").click()
    name.fill(key_name)
    expect(dialog.locator("#gk-name-error")).to_have_text(
        f"A key named {key_name} already exists. Choose another name."
    )
    dialog.get_by_role("button", name="Cancel").click()
    watch.check()


def test_settings_and_diagnostics_over_the_real_api(
    page: Page, base_url: str, app_token: str
) -> None:
    watch = Watch(page)
    sign_in_with_token(page, base_url, app_token)
    page.get_by_role("button", name="Settings", exact=True).click()
    font = page.get_by_label("Terminal font size")
    expect(font).not_to_have_value("")
    font.fill("16" if font.input_value() == "15" else "15")  # a change, on a rerun too
    font.press("Tab")
    expect(page.locator(".save-status").first).to_have_text("Saved")
    expect(page.locator(".about")).to_contain_text("C:\\Users\\john\\.ssh\\port-key")
    # A wrong current password is 401 bad_password: an error on the field, not a sign-out.
    # Wrong current passwords share one backoff for the life of the app process, so after a
    # few of them (reruns against one stack, or other specs) the answer is 429 instead.
    page.get_by_label("Current password").fill("not the password")
    page.get_by_label("New password").fill("another password")
    page.get_by_label("Confirm").fill("another password")
    page.get_by_role("button", name="Change password").click()
    expect(page.locator("#st-old-error")).to_have_text(
        re.compile(r"^(That password is not correct\. Try again\.|Too many attempts\. .*)$")
    )
    expect(page.locator("dialog.login")).to_have_count(0)

    page.get_by_role("button", name="Open Diagnostics").click()
    report = page.locator(".diag__report")
    expect(report).to_contain_text("C:\\Users\\john\\.ssh ✓ found")
    expect(report).to_contain_text("work.ppk")
    page.get_by_label("Host name or address").fill(tb.SSHD.host)
    page.get_by_role("button", name="Check", exact=True).click()
    result = page.locator(".diag__result")
    expect(result).to_contain_text(f"{tb.SSHD.host} resolves to ")
    expect(result).to_contain_text("Port 22 answered in")
    watch.check((401, "POST", "/api/auth/password"), (429, "POST", "/api/auth/password"))


def test_unbuilt_routes_degrade_quietly(page: Page, base_url: str, app_token: str) -> None:
    """Sessions, the wizard and the terminal are packages 4 to 6: the API answers 501 and the
    frontend's stubs say so, without a page error."""
    watch = Watch(page)
    sign_in_with_token(page, base_url, app_token)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    status = page.evaluate(
        "async (token) => (await fetch('/api/sessions',"
        " {headers: {Authorization: 'Bearer ' + token}})).status",
        app_token,
    )
    assert status == 501
    page.get_by_role("button", name=DIRECT, exact=True).first.click()  # open a session (8b)
    expect(page.locator("#toasts")).to_contain_text("not built yet")
    watch.check((501, "GET", "/api/sessions"))


def test_expired_token_opens_the_sign_in_dialog(
    page: Page, base_url: str, app_api: AppApi, app_token: str
) -> None:
    watch = Watch(page)
    own = app_api.login()
    sign_in_with_token(page, base_url, own)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    page.wait_for_load_state("networkidle")  # first loads and reachability are done
    assert app_api.request("POST", "/api/auth/logout", token=own).status_code == 204
    page.evaluate("import('/js/store.js').then((s) => s.refresh())")  # every read gets a 401
    login = page.locator("dialog.login")
    expect(login).to_be_visible()
    expect(login).to_contain_text("Your sign-in has ended")
    assert stored_token(page) is None
    sign_in_ui(page, tb.APP_PASSWORD)
    expect(login).to_have_count(0)
    assert stored_token(page) not in (None, own)
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.get_by_label("Terminal font size")).not_to_have_value("")
    reads = ("/api/keys", "/api/settings", "/api/connections", "/api/groups", "/api/tags")
    allowed = [(401, "GET", p) for p in (*reads, "/api/hosts/suggestions")]
    watch.check(*allowed, (401, "POST", "/api/connections/reachability"))
