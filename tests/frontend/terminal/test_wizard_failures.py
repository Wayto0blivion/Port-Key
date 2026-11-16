"""Every wizard failure state in docs/ux-spec.md with its copy and buttons (package 8b):
unreachable, refused, name not found, wrong password, password login disabled, key added but
still refused, read-only authorized_keys, not a POSIX shell, password expired, and the manual
path when "My key is already on this host" is ticked but the key is refused."""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

from tests.frontend.terminal.helpers import TermFactory, sent, tab, wait_until


def run_host(page: Page, host: str, *, port: str = "22", assume: bool = False) -> Locator:
    page.locator(".sidebar__foot").get_by_role("button", name="Add connection").click()
    wizard = page.get_by_role("dialog", name="Add connection")
    wizard.get_by_label("Host", exact=True).fill(host)
    wizard.get_by_label("Port", exact=True).fill(port)
    if assume:
        wizard.get_by_label("My key is already on this host").check()
    wizard.get_by_role("button", name="Next").click()
    return wizard


def buttons(wizard: Locator) -> Locator:
    return wizard.locator(".wizard__foot button")


def actions(page: Page) -> list[str]:
    return [m["action"] for m in sent(page, -1, "/ws/install") if m.get("type") == "action"]


def test_unreachable_then_edit_host(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "10.9.9.9", port="2222")
    expect(wizard.get_by_role("heading", name="Couldn't reach 10.9.9.9")).to_be_visible()
    expect(wizard).to_contain_text(
        "Couldn't reach 10.9.9.9 on port 2222. The connection timed out after 10 seconds. "
        "Check that the host is on and, for VPN hosts, that the VPN is connected."
    )
    expect(wizard.locator(".wsteps__item.is-problem")).to_have_text("2 Identity")
    expect(wizard.locator(".wdetails summary")).to_have_text("Details")
    expect(buttons(wizard)).to_have_text(["Cancel", "Edit host", "Try again"])
    wizard.get_by_role("button", name="Edit host").click()
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("10.9.9.9")
    expect(wizard.get_by_label("Port", exact=True)).to_have_value("2222")


def test_refused_and_try_again(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "refused-host", port="2222")
    expect(wizard).to_contain_text(
        "refused-host refused the connection on port 2222. The host is up but nothing is listening "
        "for SSH on that port. Check the port number or start sshd on the host."
    )
    wizard.get_by_role("button", name="Try again").click()
    wait_until(page, "() => __pkWs.installs.length === 2")
    expect(
        wizard.get_by_role("heading", name="refused-host refused the connection")
    ).to_be_visible()


def test_name_not_found(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "nohost")
    expect(wizard).to_contain_text(
        "Couldn't find a host named nohost. Check the spelling or use its IP address. "
        "Port-Key looks names up from inside its container, so names that exist only in "
        "the Windows hosts file may not resolve."
    )


def test_three_wrong_passwords(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "lab-01")
    field = wizard.get_by_label("Password for john@lab-01")
    alert = wizard.locator(".wstep__error")
    for attempt in range(3):
        if attempt:
            expect(alert).to_contain_text("rejected the password")  # the retry prompt
            expect(field).to_have_value("")
        expect(field).to_be_focused()
        field.fill(f"nope {attempt}")
        page.keyboard.press("Enter")
        if attempt < 2:
            wait_until(
                page,
                "(n) => document.querySelectorAll('#wz-password:not([disabled])').length && "
                "__pkWs.sockets.at(-1).sent.filter((m) => m.type === 'prompt_reply').length === n",
                attempt + 1,
            )
    expect(wizard.get_by_role("heading", name="Wrong password")).to_be_visible()
    expect(wizard).to_contain_text(
        "lab-01 rejected the password for john. Check the user name and password, then try again."
    )
    expect(wizard.locator(".wsteps__item.is-problem")).to_have_text("3 Sign in")


def test_password_login_disabled(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "nopass-host")
    expect(
        wizard.get_by_role("heading", name="nopass-host does not accept passwords")
    ).to_be_visible()
    expect(wizard).to_contain_text(
        "The server only allows key logins, so Port-Key can't install your key for you. "
        "Add the key on the host yourself, for example from its console, then check again."
    )
    box = wizard.get_by_label("Run this on nopass-host as john")
    expect(box).to_have_value(
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo 'ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeForTestsOnly10 john@WORKPC' "
        ">> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )
    wizard.get_by_role("button", name="Copy install command").click()
    expect(page.locator("#toasts")).to_contain_text("Install command copied")
    wizard.get_by_role("button", name="Copy public key only").click()
    expect(page.locator("#toasts")).to_contain_text("Public key copied")
    assert page.evaluate("navigator.clipboard.readText()").startswith("ssh-ed25519 ")
    expect(buttons(wizard)).to_have_text(["Back", "Cancel", "Check key login"])
    wizard.get_by_role("button", name="Check key login").click()
    expect(wizard.get_by_role("button", name="Continue")).to_be_visible()
    assert actions(page) == ["check_key"]


def test_key_still_refused_then_fix_permissions(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "badperms-host")
    wizard.get_by_label("Password for john@badperms-host").fill("pa55word")
    page.keyboard.press("Enter")
    expect(
        wizard.get_by_role("heading", name="badperms-host still refused the key")
    ).to_be_visible()
    expect(wizard).to_contain_text(
        "Port-Key added your key, but badperms-host still refused it. "
        "The usual cause is permissions: sshd ignores authorized_keys if other users can "
        "write to it or to ~/.ssh."
    )
    expect(wizard).to_contain_text("runs chmod go-w on ~/.ssh and ~/.ssh/authorized_keys as john")
    wizard.locator(".wdetails summary").click()
    expect(wizard.locator(".wdetails__text")).to_contain_text("bad ownership or modes")
    wizard.get_by_role("button", name="Fix permissions and retry").click()
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
    expect(wizard.locator(".wcheck__line--warn")).to_contain_text(
        "Added key to ~/.ssh/authorized_keys"
    )
    assert actions(page) == ["fix_permissions"]


def test_read_only_authorized_keys(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "ro-host")
    wizard.get_by_label("Password for john@ro-host").fill("pa55word")
    page.keyboard.press("Enter")
    expect(
        wizard.get_by_role("heading", name="Port-Key couldn't write authorized_keys")
    ).to_be_visible()
    # The server's text carries the reason; the remote output is in Details.
    expect(wizard).to_contain_text(
        "Port-Key signed in but couldn't write ~/.ssh/authorized_keys (Read-only file system)."
    )
    expect(wizard.locator(".wcheck")).to_have_count(0)
    expect(buttons(wizard)).to_have_text(["Back", "Cancel", "Save as password-only"])
    wizard.get_by_role("button", name="Save as password-only").click()
    expect(wizard).to_contain_text("ro-host is saved with password sign-in.")
    assert actions(page) == ["save_password_only"]


def test_not_a_posix_shell(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "win-host")
    expect(wizard).to_contain_text(
        "win-host doesn't offer a standard shell, so Port-Key can't install the key for you. "
        "Add the public key on the host yourself, then select Check key login."
    )
    expect(wizard).to_contain_text("C:\\ProgramData\\ssh\\administrators_authorized_keys")
    expect(wizard.locator(".wstep").get_by_role("button")).to_have_text(["Copy public key"])
    expect(buttons(wizard)).to_have_text(["Back", "Cancel", "Check key login"])


def test_a_rejected_start_goes_back_to_the_form(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "taken-host")
    expect(wizard.get_by_label("Host", exact=True)).to_have_value("taken-host")
    expect(wizard.locator(".modal__error")).to_have_text(
        "A connection named taken-host already exists. Choose another name."
    )
    expect(wizard.locator(".wsteps__item.is-current")).to_have_text("1 Host")


def test_password_expired_opens_a_password_session(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "expired-host")
    wizard.get_by_label("Password for john@expired-host").fill("pa55word")
    page.keyboard.press("Enter")
    expect(wizard).to_contain_text(
        "john's password on expired-host has expired and must be changed. "
        "Open a password session to change it, then run this wizard again."
    )
    wizard.get_by_role("button", name="Open password session").click()
    expect(wizard).to_be_hidden()
    expect(tab(page, "expired-host")).to_be_visible()
    opens = [m for m in sent(page) if m.get("type") == "open"]
    assert opens[0]["force_password"] is True


def test_manual_path_when_the_key_is_refused(term: TermFactory) -> None:
    page = term()
    wizard = run_host(page, "lab-01", assume=True)
    expect(wizard.get_by_role("heading", name="Key login was refused")).to_be_visible()
    expect(wizard.locator(".wstep").get_by_role("button")).to_have_text(
        ["Copy public key", "Copy install command"]
    )
    expect(buttons(wizard)).to_have_text(
        ["Back", "Cancel", "Save as password-only", "Install it with a password"]
    )
    assert page.evaluate("__pkWs.installs[0].assume_installed") is True
    wizard.get_by_role("button", name="Install it with a password").click()
    expect(wizard.get_by_role("heading", name="Sign in once")).to_be_visible()
    wizard.get_by_label("Password for john@lab-01").fill("pa55word")
    page.keyboard.press("Enter")
    expect(wizard.get_by_role("heading", name="Connection saved")).to_be_visible()
