"""Boot, sign-in, the 401 path, tabs, shortcuts and the narrow drawer (package 8a)."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.frontend.conftest import AppFactory, expect_toast

PASSWORD = "correct horse battery"


def open_menu_item(page: Page, row_name: str, item: str) -> None:
    page.get_by_role("button", name=f"{row_name} menu").last.click()
    page.get_by_role("menuitem", name=item).click()


def test_boot_renders_the_shell(app: AppFactory) -> None:
    page = app()
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    expect(page.locator(".tree [role=treeitem]").first).to_be_visible()
    expect(page.locator(".unlocked")).to_have_text("1 key unlocked")
    expect(page.get_by_role("button", name="Lock")).to_be_visible()
    page.get_by_role("button", name="Lock").click()
    expect(page.locator(".unlocked")).to_be_hidden()
    expect_toast(page, "Keys locked.")


def test_set_password_on_first_visit(app: AppFactory) -> None:
    page = app("setup", signed_in=False)
    dialog = page.locator("dialog.login")
    expect(dialog.get_by_role("heading")).to_have_text("Set a password for Port-Key")
    # The setup code comes first, with where to find it (docs/design/SetPassword.dc.html).
    code = dialog.get_by_label("Setup code")
    expect(code).to_be_focused()
    expect(code).to_have_attribute("placeholder", "XXXX-XXXX-XXXX")
    expect(page.locator("#login-code-help")).to_have_text(
        "Port-Key printed this code when it started. To see it, run "
        "docker compose logs portkey on this PC."
    )
    labels = dialog.locator(".field__label")
    expect(labels).to_have_text(["Setup code", "Password", "Confirm password"])
    page.fill("#login-pass", PASSWORD)
    page.fill("#login-confirm", PASSWORD)
    page.click("text=Set password")
    expect(page.locator("#login-code-error")).to_have_text(
        "Enter the setup code. docker compose logs portkey shows it."
    )
    expect(code).to_be_focused()
    code.fill("k7qm 3xrp w9td")  # any case, any separators
    page.fill("#login-pass", "short")
    page.fill("#login-confirm", "short")
    page.click("text=Set password")
    expect(page.locator("#login-code-error")).to_be_hidden()
    expect(page.locator("#login-pass-error")).to_have_text("Use at least 8 characters.")
    page.fill("#login-pass", PASSWORD)
    page.fill("#login-confirm", "something else")
    page.keyboard.press("Enter")
    expect(page.locator("#login-confirm-error")).to_have_text("The passwords don't match.")
    page.fill("#login-confirm", PASSWORD)
    page.keyboard.press("Enter")
    expect(dialog).to_have_count(0)
    expect(page.locator(".app .tree")).to_be_visible()
    assert page.evaluate("localStorage.getItem('portkey.token')").startswith("mock-")


def test_wrong_setup_code_shows_inline_and_keeps_the_password(app: AppFactory) -> None:
    page = app("setup", signed_in=False)
    code = page.get_by_label("Setup code")
    code.fill("AAAA-BBBB-CCCC")
    page.fill("#login-pass", PASSWORD)
    page.fill("#login-confirm", PASSWORD)
    page.keyboard.press("Enter")
    expect(page.locator("#login-code-error")).to_have_text(
        "That setup code is not correct. Use the newest code in the log "
        "(docker compose logs portkey)."
    )
    expect(code).to_have_attribute("aria-invalid", "true")
    expect(code).to_be_focused()
    expect(page.locator("#login-pass")).to_have_value(PASSWORD)
    expect(page.locator("#login-confirm")).to_have_value(PASSWORD)
    code.fill("K7QM-3XRP-W9TD")
    page.keyboard.press("Enter")
    expect(page.locator("dialog.login")).to_have_count(0)
    setup = page.evaluate(
        "window.__pkMock.calls.filter((c) => c.path === '/api/auth/setup').map((c) => c.body)"
    )
    assert setup == [{}, {}]  # the mock records no code and no password


def test_wrong_setup_codes_hit_the_rate_limit(app: AppFactory) -> None:
    page = app("setup", signed_in=False)
    for _ in range(2):
        page.get_by_label("Setup code").fill("AAAA-BBBB-CCCC")
        page.fill("#login-pass", PASSWORD)
        page.fill("#login-confirm", PASSWORD)
        page.keyboard.press("Enter")
        expect(page.locator("#login-code-error")).to_contain_text("is not correct")
    page.get_by_label("Setup code").fill("AAAA-BBBB-DDDD")
    page.keyboard.press("Enter")
    expect(page.locator(".login__error")).to_have_text(
        "Too many attempts. Try again in 30 seconds."
    )
    expect(page.get_by_role("button", name="Set password")).to_be_disabled()


def test_setup_after_a_reset_asks_for_a_restart(app: AppFactory) -> None:
    page = app("setup-restart", signed_in=False)
    notice = page.locator(".login__error")
    restart = (
        "Port-Key has no setup code right now: its password was cleared while it was running. "
        "Restart it on this PC (docker compose restart portkey), then use the code that "
        "docker compose logs portkey shows."
    )
    expect(notice).to_have_text(restart)
    page.get_by_label("Setup code").fill("K7QM-3XRP-W9TD")
    page.fill("#login-pass", PASSWORD)
    page.fill("#login-confirm", PASSWORD)
    page.keyboard.press("Enter")
    expect(notice).to_have_text(restart)
    expect(page.locator("dialog.login")).to_be_visible()


def test_sign_in_errors_and_rate_limit(app: AppFactory) -> None:
    page = app("signedout", signed_in=False)
    expect(page.locator("#login-title")).to_have_text("Sign in to Port-Key")
    error = page.locator(".login__error")
    for _ in range(2):
        page.fill("#login-pass", "wrong password")
        page.keyboard.press("Enter")
        expect(error).to_have_text("That password is not correct. Try again.")
        expect(page.locator("#login-pass")).to_have_value("")
        expect(page.locator("#login-pass")).to_be_focused()
    page.fill("#login-pass", "wrong again")
    page.keyboard.press("Enter")
    expect(error).to_have_text("Too many attempts. Try again in 30 seconds.")
    expect(page.get_by_role("button", name="Sign in")).to_be_disabled()


def test_sign_in_then_shell(app: AppFactory) -> None:
    page = app("signedout", signed_in=False)
    page.fill("#login-pass", PASSWORD)
    page.keyboard.press("Enter")
    expect(page.locator(".app .tree [role=treeitem]").first).to_be_visible()


def test_401_keeps_unsaved_input(app: AppFactory) -> None:
    page = app()
    open_menu_item(page, "web-prod", "Edit")
    page.fill("#ec-name", "web-prod-new")
    page.evaluate("window.__pkMock.expire()")
    page.get_by_role("button", name="Save", exact=True).click()
    login = page.locator("dialog.login")
    expect(login).to_be_visible()
    expect(login).to_contain_text("Your sign-in has ended")
    expect(page.locator("dialog.modal .modal__error")).to_have_text(
        "Your sign-in expired. Sign in again, then select Save."
    )
    page.fill("#login-pass", PASSWORD)
    page.keyboard.press("Enter")
    expect(login).to_have_count(0)
    expect(page.locator("#ec-name")).to_have_value("web-prod-new")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("dialog.modal")).to_have_count(0)
    expect_toast(page, "Saved changes to web-prod-new.")
    expect(page.locator(".tree").get_by_text("web-prod-new").first).to_be_visible()


def test_tabs_and_shortcuts(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Keys", exact=True).click()
    page.get_by_role("button", name="Settings", exact=True).click()
    tabs = page.get_by_role("tab")
    expect(tabs).to_have_text(["", "Keys", "Settings"])
    expect(page.get_by_role("tab", name="Settings")).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Alt+0")
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Alt+2")
    expect(page.get_by_role("tab", name="Settings")).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(page.get_by_role("tab", name="Keys")).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Delete")
    expect(tabs).to_have_count(2)
    expect(page.get_by_role("tab", name="Keys")).to_be_focused()  # focus moves to the left tab
    page.get_by_role("tab", name="Keys").click(button="middle")
    expect(tabs).to_have_count(1)
    expect(page.get_by_role("tab", name="Home")).to_have_attribute("aria-selected", "true")


def test_slash_focuses_filter_and_panels_stay_mounted(app: AppFactory) -> None:
    page = app()
    page.get_by_role("button", name="Keys", exact=True).click()
    page.locator("body").press("/")
    expect(page.locator("#sidebar-filter")).to_be_focused()
    marker = page.evaluate("document.querySelector('[data-panel=keys]').dataset.marker = 'kept'")
    assert marker == "kept"
    page.keyboard.press("Alt+0")
    page.keyboard.press("Alt+1")
    assert page.evaluate("document.querySelector('[data-panel=keys]').dataset.marker") == "kept"


def test_sidebar_resize_by_keyboard_persists(app: AppFactory) -> None:
    page = app()
    sep = page.get_by_role("separator", name="Resize sidebar")
    sep.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    expect(sep).to_have_attribute("aria-valuenow", "312")
    page.keyboard.press("End")
    expect(sep).to_have_attribute("aria-valuenow", "420")
    page.reload()
    expect(page.get_by_role("separator", name="Resize sidebar")).to_have_attribute(
        "aria-valuenow", "420"
    )
    width = page.evaluate("document.getElementById('sidebar').getBoundingClientRect().width")
    assert width == 420


def test_narrow_window_drawer(app: AppFactory) -> None:
    page = app(width=800)
    menu = page.get_by_role("button", name="Connections")
    expect(page.locator("#sidebar-filter")).not_to_be_visible()
    assert page.evaluate("document.getElementById('sidebar').inert") is True
    menu.click()
    expect(menu).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#sidebar-filter")).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_focused()
    page.locator("body").press("/")
    expect(page.locator("#sidebar-filter")).to_be_focused()
    page.click(".drawer-backdrop", position={"x": 700, "y": 400})
    expect(menu).to_have_attribute("aria-expanded", "false")


def test_quick_connect_reaches_the_palette(app: AppFactory) -> None:
    page = app()
    page.keyboard.press("Control+Shift+K")
    # Until package 8b lands the palette is a stub; either it opens or the shell says so.
    page.wait_for_selector("dialog[open], #toasts .toast")
    if page.locator("#toasts .toast").count():
        expect_toast(page, "not built yet")
