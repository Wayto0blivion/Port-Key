"""Screenshots of the real app against the sshd test bed, at 1440 x 900 (package 9 tool).

Not part of the pytest suite. Run it in the e2e image on the compose network with an output
folder mounted, nothing published:

    docker compose -f compose.dev.yaml -p portkey-wp9 --profile e2e up -d --wait app
    docker compose -f compose.dev.yaml -p portkey-wp9 --profile e2e run --rm --no-deps \
        -v /abs/out:/shots e2e python -m tests.e2e.screens /shots
    docker compose -f compose.dev.yaml -p portkey-wp9 --profile e2e down -v

It seeds folders and connections through the API (host names are the test bed's), then saves
Home, a terminal, Keys, Settings, Diagnostics, each wizard step, the password-login-disabled
panel, the passphrase prompt and both host key dialogs. Compare them with docs/design/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.conftest import browser_args
from tests.e2e.harness.app_api import AppApi
from tests.e2e.helpers import Api, connected, open_row, reset_ssh_user, type_command
from tests.fixtures import testbed as tb

BASE = os.environ.get("PORTKEY_BASE_URL", "http://localhost:8822")


def seed(api: Api) -> None:
    groups = {g["name"]: g for g in api.call("GET", "/api/groups")["groups"]}

    def group(name: str, parent: str | None = None) -> str:
        if name not in groups:
            body: dict[str, Any] = {"name": name}
            if parent:
                body["parent_id"] = groups[parent]["id"]
            groups[name] = api.call("POST", "/api/groups", body)
        return str(groups[name]["id"])

    office, servers, lab = group("Office LAN"), group("Servers", "Office LAN"), group("Lab")
    web = api.ensure(
        "web-prod",
        host=tb.SSHD.host,
        username="keyonly",
        color="red",
        group_id=servers,
        tags=["prod", "web"],
        pinned=True,
        notes="Production. Red on purpose.",
    )
    api.ensure(
        "build-box",
        host=tb.SSHD.host,
        username="keyonly",
        color="blue",
        group_id=servers,
        tags=["build", "linux"],
    )
    api.ensure(
        "backup-nas",
        host=tb.SSHD.host,
        port=2222,
        username="keyonly",
        color="purple",
        group_id=office,
        tags=["backup"],
    )
    api.ensure(
        "print-srv",
        host=tb.SSHD.host,
        username="pw",
        auth_method="password",
        group_id=office,
        tags=["legacy"],
        notes="Password only",
    )
    api.ensure(
        "parser-vm",
        host=tb.SSHD_INNER.host,
        username="keyonly",
        color="amber",
        group_id=lab,
        jump_id=web["id"],
        tags=["linux"],
    )
    api.ensure(
        "old-gateway", host="gateway.invalid", username="admin", color="green", tags=["network"]
    )


def shot(page: Page, out: Path, name: str) -> None:
    page.wait_for_timeout(300)  # let transitions settle
    page.screenshot(path=str(out / f"{name}.png"))
    print(f"saved {name}.png")


def add_connection(page: Page, host: str, user: str, name: str) -> Any:
    page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    wizard.locator("#wz-host").fill(host)
    wizard.locator("#wz-user").fill(user)
    wizard.locator("#wz-name").fill(name)
    return wizard


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    app_api = AppApi(BASE)
    app_api.wait_healthy()
    token = app_api.ensure_password()
    api = Api(app_api, token)
    api.kill_sessions()
    api.call("POST", "/api/keys/lock")
    seed(api)
    for host in (tb.SSHD_REKEYED.host, "localhost"):
        api.forget_host_keys(host)
    for name in ("shot-new", "shot-fresh", "shot-keyonly", "shot-local-a", "shot-local-b"):
        api.delete_connection(name)
    api.delete_connection("shot-jump-b")
    reset_ssh_user("fresh", server=tb.SSHD_REKEYED)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=browser_args(BASE))
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(script=f"localStorage.setItem('portkey.token', {token!r});")
        page = context.new_page()
        page.goto(BASE + "/")
        page.wait_for_selector(".app .tabstrip [role=tab]")
        expect(page.locator(".dot--up").first).to_be_visible(timeout=15_000)
        shot(page, out, "home")
        page.set_viewport_size({"width": 1280, "height": 800})  # a laptop at 150 %
        shot(page, out, "home-1280")
        page.set_viewport_size({"width": 1440, "height": 900})

        open_row(page, "web-prod")
        connected(page)
        type_command(
            page, "ls --color=always -la /etc | head -n 12; echo done-$((40+2))", "done-42"
        )
        shot(page, out, "terminal")

        page.locator(".topbar").get_by_role("button", name="Keys", exact=True).click()
        expect(page.locator(".key-detail")).to_be_visible()
        shot(page, out, "keys")
        page.locator(".topbar").get_by_role("button", name="Settings", exact=True).click()
        expect(page.get_by_label("Terminal font size")).not_to_have_value("")
        shot(page, out, "settings")
        page.get_by_role("button", name="Open Diagnostics").click()
        expect(page.locator(".diag__report")).to_be_visible()
        shot(page, out, "diagnostics")
        page.locator(".tabstrip [role=tab]").first.click()

        # The wizard on a host whose key is not known yet, as a password user.
        wizard = add_connection(page, tb.SSHD_REKEYED.host, "fresh", "shot-fresh")
        shot(page, out, "wizard-1-host")
        wizard.get_by_role("button", name="Next").click()
        expect(wizard.get_by_role("button", name="Trust this host")).to_be_visible(timeout=20_000)
        shot(page, out, "wizard-2-identity")
        wizard.get_by_role("button", name="Trust this host").click()
        password = wizard.locator("#wz-password:not([disabled])")
        expect(password).to_be_visible(timeout=20_000)
        shot(page, out, "wizard-3-signin")
        password.fill(tb.USERS["fresh"].password)
        wizard.get_by_role("button", name="Install key").click()
        expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible(timeout=30_000)
        shot(page, out, "wizard-4-saved")
        page.keyboard.press("Escape")

        wizard = add_connection(page, tb.SSHD.host, "keyonly", "shot-keyonly")
        wizard.locator("#wz-auth").select_option(label="id_ed25519_portkey")
        wizard.get_by_role("button", name="Next").click()
        expect(wizard.get_by_role("button", name="Check key login")).to_be_visible(timeout=20_000)
        shot(page, out, "wizard-password-disabled")
        wizard.get_by_role("button", name="Cancel").click()

        # Connection-time dialogs.
        locked = api.key("id_ed25519_pass")
        api.ensure("shot-locked", host=tb.SSHD.host, username="keyonly", key_fp=locked["fp"])
        page.reload()
        page.wait_for_selector(".app .tabstrip [role=tab]")
        open_row(page, "shot-locked")
        unlock = page.get_by_role("dialog", name="Unlock id_ed25519_pass")
        expect(unlock).to_be_visible()
        shot(page, out, "passphrase")
        unlock.get_by_role("button", name="Cancel").click()

        jump_a = api.connections()["web-prod"]
        jump_b = api.ensure("shot-jump-b", host=tb.SSHD_REKEYED.host, username="keyonly")
        api.ensure("shot-local-a", host="localhost", username="keyonly", jump_id=jump_a["id"])
        api.ensure("shot-local-b", host="localhost", username="keyonly", jump_id=jump_b["id"])
        page.reload()
        page.wait_for_selector(".app .tabstrip [role=tab]")
        open_row(page, "shot-local-a")
        dialog = page.get_by_role("dialog", name="Check this host's identity")
        expect(dialog).to_be_visible(timeout=15_000)
        shot(page, out, "hostkey-new")
        dialog.get_by_role("button", name="Trust this host").click()
        connected(page)
        open_row(page, "shot-local-b")
        changed = page.get_by_role("dialog", name="shot-local-b's host key has changed")
        expect(changed).to_be_visible(timeout=15_000)
        changed.get_by_role("textbox").fill("shot-local-b")
        shot(page, out, "hostkey-changed")
        changed.get_by_role("button", name="Keep it closed").click()
        browser.close()
    reset_ssh_user("fresh", server=tb.SSHD_REKEYED)  # the wizard installed a key there
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/portkey-shots")))
