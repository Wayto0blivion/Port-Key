"""Copy, paste and the shortcut rule inside a terminal (package 8b): Ctrl+C copies with a
selection and interrupts without, Ctrl+V and Shift+Insert paste, right-click copies or pastes,
the multi-line paste question, the Windows Terminal setting, and which keys reach the shell."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.frontend.terminal.helpers import (
    TermFactory,
    connected,
    panel,
    run,
    session_ids,
    wait_until,
)


def shell_input(page: Page) -> str:
    text: str = page.evaluate("(id) => __pkWs.inputOf(id)", session_ids(page)[0])
    return text


def focus(page: Page) -> None:
    panel(page).locator(".xterm-helper-textarea").focus()


def select_welcome(page: Page) -> None:
    run(
        page,
        "views/sessions.js",
        "m.viewFor(document.querySelector('.tabpanel:not([hidden])').dataset.panel)"
        ".term.select(0, 0, 7)",
    )


def clipboard(page: Page) -> str:
    text: str = page.evaluate("navigator.clipboard.readText()")
    return text


def set_clipboard(page: Page, text: str) -> None:
    page.evaluate("(t) => navigator.clipboard.writeText(t)", text)


def test_ctrl_c_copies_a_selection_and_interrupts_without(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    select_welcome(page)
    focus(page)
    page.keyboard.press("Control+C")
    wait_until(page, "() => navigator.clipboard.readText().then((t) => t === 'Welcome')")
    assert "\x03" not in shell_input(page)
    assert not run(
        page,
        "views/sessions.js",
        "m.viewFor(document.querySelector('.tabpanel:not([hidden])').dataset.panel).term.hasSelection()",
    )
    page.keyboard.press("Control+C")
    wait_until(page, "(id) => __pkWs.inputOf(id).includes('\\x03')", session_ids(page)[0])
    expect(panel(page).locator(".xterm-rows")).to_contain_text("^C")


def test_ctrl_v_pastes_one_line_without_asking(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    set_clipboard(page, "echo pasted")
    focus(page)
    page.keyboard.press("Control+V")
    wait_until(page, "(id) => __pkWs.inputOf(id).includes('echo pasted')", session_ids(page)[0])
    expect(page.locator("dialog[open]")).to_have_count(0)


def test_multi_line_paste_asks_first(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    set_clipboard(page, "echo a\necho b\necho c\n")
    focus(page)
    page.keyboard.press("Shift+Insert")
    dialog = page.get_by_role("dialog", name="Paste 3 lines into web-prod?")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(panel(page).locator(".xterm-helper-textarea")).to_be_focused()
    assert "echo a" not in shell_input(page)
    page.keyboard.press("Control+V")
    page.get_by_role("dialog", name="Paste 3 lines into web-prod?").get_by_role(
        "button", name="Paste"
    ).click()
    wait_until(
        page,
        "(id) => __pkWs.inputOf(id).includes('echo a\\recho b\\recho c\\r')",
        session_ids(page)[0],
    )


def test_right_click_copies_then_pastes(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    select_welcome(page)
    screen = panel(page).locator(".xterm-screen")
    screen.click(button="right")
    wait_until(page, "() => navigator.clipboard.readText().then((t) => t === 'Welcome')")
    set_clipboard(page, "echo from menu")
    screen.click(button="right")
    wait_until(page, "(id) => __pkWs.inputOf(id).includes('echo from menu')", session_ids(page)[0])


def test_ctrl_insert_always_copies(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    select_welcome(page)
    focus(page)
    page.keyboard.press("Control+Insert")
    wait_until(page, "() => navigator.clipboard.readText().then((t) => t === 'Welcome')")


def test_windows_copy_paste_off_sends_ctrl_c_and_ctrl_v(term: TermFactory) -> None:
    page = term()
    page.evaluate(
        "async () => { const s = await import('/js/store.js');"
        " s.setState({settings: {...s.getState().settings, windows_copy_paste: false}}); }"
    )
    connected(page, "web-prod")
    select_welcome(page)
    focus(page)
    page.keyboard.press("Control+C")
    page.keyboard.press("Control+V")
    wait_until(page, "(id) => __pkWs.inputOf(id).includes('\\x03\\x16')", session_ids(page)[0])


def test_shortcut_rule(term: TermFactory) -> None:
    page = term()
    connected(page, "web-prod")
    focus(page)
    page.keyboard.press("Control+K")  # the shell's kill-line, not the palette
    page.keyboard.press("Alt+b")  # readline: back one word
    page.keyboard.press("Escape")
    wait_until(
        page, "(id) => __pkWs.inputOf(id).endsWith('\\x0b\\x1bb\\x1b')", session_ids(page)[0]
    )
    expect(page.locator("dialog[open]")).to_have_count(0)
    page.keyboard.press("Control+Shift+F")
    expect(panel(page).get_by_role("searchbox", name="Find in scrollback")).to_be_focused()
    page.keyboard.press("Escape")
    page.keyboard.press("Control+Shift+K")
    expect(page.get_by_role("dialog", name="Quick connect")).to_be_visible()
    assert shell_input(page).endswith("\x0b\x1bb\x1b")  # nothing else reached the shell
