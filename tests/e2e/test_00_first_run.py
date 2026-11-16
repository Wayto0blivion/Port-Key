"""First visit: read the setup code from the app's log, set the app password, then sign in
again (docs/ux-spec.md "Set password").

Must sort first and must not use ``app_token``: once the password is set it stays set for the
rest of the run. On a stack that already has a password (a rerun without ``./mk e2e``) it
skips, since there is no first visit left to test.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.harness.app_api import AppApi
from tests.e2e.harness.app_log import app_log, setup_code_from_log
from tests.e2e.harness.browser import set_app_password_ui, sign_in_ui, stored_token
from tests.e2e.helpers import CSP_WATCH, Watch
from tests.fixtures import testbed as tb


def test_the_log_shows_the_setup_code_in_one_framed_record(app_api: AppApi) -> None:
    if app_api.auth_status()["setup"]:
        pytest.skip("the app password is already set; the first visit needs a fresh ./mk e2e")
    log = app_log()
    code = setup_code_from_log(log)
    # What the owner sees in `docker compose logs portkey`: the code, what it is for and where
    # to type it, between two rules, printed once.
    assert "Port-Key has no app password yet. Open http://localhost:8822 on this PC" in log
    assert '"Setup code" field' in log
    assert log.count("=" * 72) == 2
    assert log.count(code) == 1
    # The API says a code is needed, never what it is.
    status = app_api.request("GET", "/api/auth/status")
    assert status.json() == {"setup": False, "authenticated": False, "setup_code": "required"}
    assert code not in status.text


def test_first_visit_sets_the_password_and_signs_in(
    page: Page, base_url: str, app_api: AppApi
) -> None:
    if app_api.auth_status()["setup"]:
        pytest.skip("the app password is already set; the first visit needs a fresh ./mk e2e")
    code = setup_code_from_log()
    page.context.add_init_script(script=CSP_WATCH)
    watch = Watch(page)
    page.goto(base_url + "/")
    expect(page.locator("#login-title")).to_have_text("Set a password for Port-Key")
    dialog = page.locator("dialog.login")
    expect(dialog).to_contain_text(
        "Port-Key can open shells on your servers, so it asks for a password even though it "
        "only listens on this PC."
    )
    expect(dialog).to_contain_text(
        "Port-Key printed this code when it started. To see it, run "
        "docker compose logs portkey on this PC."
    )

    # Checked in the browser before anything is sent.
    set_app_password_ui(page, "short", code)
    expect(dialog).to_contain_text("Use at least 8 characters.")
    page.get_by_label("Password", exact=True).fill(tb.APP_PASSWORD)
    page.get_by_label("Confirm password", exact=True).fill(tb.APP_PASSWORD + "x")
    page.get_by_role("button", name="Set password").click()
    expect(dialog).to_contain_text("The passwords don't match.")
    assert app_api.auth_status()["setup"] is False

    # A wrong code is refused by the app and shown under the code field.
    set_app_password_ui(page, tb.APP_PASSWORD, "AAAA-BBBB-CCCC")
    expect(page.locator("#login-code-error")).to_have_text(
        "That setup code is not correct. Use the newest code in the log "
        "(docker compose logs portkey)."
    )
    expect(page.get_by_label("Password", exact=True)).to_have_value(tb.APP_PASSWORD)
    assert app_api.auth_status()["setup"] is False

    # The right one, typed loosely: lower case, spaces instead of dashes.
    page.get_by_label("Setup code", exact=True).fill(code.lower().replace("-", " "))
    page.get_by_role("button", name="Set password").click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    # Nothing saved yet: the first-run card names the default key by its Windows path.
    first_run = page.locator(".empty--firstrun")
    expect(first_run).to_contain_text("No connections yet")
    expect(first_run).to_contain_text("id_ed25519")
    expect(first_run).to_contain_text("C:\\Users\\john\\.ssh")
    token = stored_token(page)
    assert token
    assert app_api.auth_status(token) == {
        "setup": True,
        "authenticated": True,
        "setup_code": None,
    }

    # Setup is one-time: the code is used up, and a second setup is refused.
    again = app_api.setup_raw("another password", code)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_setup")

    # A browser without the token gets the sign-in screen, and the password works there.
    page.evaluate("() => localStorage.clear()")
    page.reload()
    expect(page.locator("#login-title")).to_have_text("Sign in to Port-Key")
    expect(page.get_by_label("Setup code", exact=True)).to_have_count(0)
    sign_in_ui(page, tb.APP_PASSWORD)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    assert stored_token(page) not in (None, token)
    watch.check((403, "POST", "/api/auth/setup"))
