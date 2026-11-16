"""The install wizard against the sshd test bed: every user, then idempotency."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from portkey.services import Services
from portkey.ssh.errors import AuthFailed, PromptCancelled
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter, kbdint_answers
from tests.integration.wizard.helpers import installed_line, remote_keys, run_err, run_ok, start

Cleanup = list[tuple[str, tb.SshServer, tb.SshServer | None]]


def pw(user: str) -> str:
    return tb.USERS[user].password


def trail(io: Any) -> list[tuple[str, str, str | None]]:
    return [t for t in io.trail() if t[0] not in ("validate", "connect")]


async def test_fresh_full_path_then_second_run(
    svc: Services, cleanup: Cleanup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cleanup.append(("fresh", tb.SSHD, None))
    prompter = FakePrompter(password=pw("fresh"))
    record, outcome, io = await run_ok(svc, start("fresh"), prompter)
    assert outcome == "installed"
    assert trail(io) == [
        ("hostkey", "running", None),
        ("hostkey", "ok", "known"),
        ("try_key", "running", None),
        ("try_key", "warn", "refused"),
        ("password_login", "running", None),
        ("password_login", "ok", None),
        ("install", "running", None),
        ("install", "ok", "added"),
        ("verify", "running", None),
        ("verify", "ok", None),
        ("discard_password", "ok", None),
        ("save", "running", None),
        ("save", "ok", None),
    ]
    assert prompter.kinds() == ["password"]
    text, modes = await remote_keys("fresh")
    assert text == installed_line(svc) + "\n"
    assert modes == "700 600"
    saved = svc.connections.get(record.id)
    assert (saved.auth_method, saved.host, saved.username, saved.tags) == (
        "key",
        "sshd",
        "fresh",
        ("test",),
    )
    assert [e.kind for e in svc.events.recent()] == ["key_installed"]
    assert pw("fresh") not in caplog.text
    assert pw("fresh") not in repr(svc.db.query("SELECT * FROM events"))

    # Second run: the key already works, so nothing is asked and nothing changes.
    again = FakePrompter()
    _, outcome, io = await run_ok(svc, start("fresh", name="sshd-fresh-2"), again)
    assert outcome == "already_installed"
    assert ("try_key", "ok", "already_installed") in io.trail()
    assert again.calls == []
    assert await remote_keys("fresh") == (text, modes)


async def test_wrong_password_then_right_password(svc: Services, cleanup: Cleanup) -> None:
    cleanup.append(("fresh", tb.SSHD, None))
    prompter = FakePrompter(password=["TEST-ONLY wrong password", pw("fresh")])
    _, outcome, _ = await run_ok(svc, start("fresh"), prompter)
    assert outcome == "installed"
    assert [c.detail["retry"] for c in prompter.calls] == [False, True]


async def test_three_wrong_passwords(svc: Services) -> None:
    prompter = FakePrompter(password="TEST-ONLY wrong password")
    exc, io = await run_err(svc, start("fresh"), prompter)
    assert isinstance(exc, AuthFailed)
    assert io.trail()[-1] == ("password_login", "fail", "auth_failed")
    assert svc.connections.list_all() == []


async def test_password_only_user_installs_but_key_login_stays_off(
    svc: Services, cleanup: Cleanup
) -> None:
    cleanup.append(("pw", tb.SSHD, None))
    prompter = FakePrompter(password=pw("pw"))
    record, outcome, io = await run_ok(svc, start("pw"), prompter, ["save_password_only"])
    assert outcome == "saved_password_only"
    [asked] = io.asked
    assert asked.code == "key_still_refused"
    assert asked.actions == ["save_password_only", "cancel"]  # fixing modes would not help
    assert ("install", "ok", "added") in io.trail()
    assert svc.connections.get(record.id).auth_method == "password"
    first, _ = await remote_keys("pw")

    # Second run: the script finds the key and changes nothing.
    exc, io = await run_err(svc, start("pw", name="sshd-pw-2"), prompter, ["cancel"])
    assert isinstance(exc, PromptCancelled)
    assert ("install", "ok", "already_present") in io.trail()
    assert await remote_keys("pw") == (first, "700 600")
    assert first == installed_line(svc) + "\n"


async def test_keyonly_with_an_installed_key(svc: Services) -> None:
    prompter = FakePrompter()
    _, outcome, io = await run_ok(svc, start("keyonly"), prompter)
    assert outcome == "already_installed"
    assert prompter.calls == []
    assert ("password_login", "running", None) not in io.trail()


async def test_keyonly_with_the_unknown_key(svc: Services) -> None:
    unknown = tb.fingerprint(tb.client_public("id_ed25519_portkey"))
    msg = start("keyonly", key_fp=unknown)
    exc, io = await run_err(svc, msg, FakePrompter(), ["check_key", "cancel"])
    assert isinstance(exc, PromptCancelled)
    assert [a.code for a in io.asked] == ["password_not_allowed", "password_not_allowed"]
    asked = io.asked[0]
    assert asked.actions == ["check_key", "cancel"]
    blob = tb.client_public("id_ed25519_portkey").split()[1]
    assert asked.public_key is not None
    assert blob in asked.public_key
    assert asked.install_command is not None
    assert blob in asked.install_command
    assert svc.connections.list_all() == []


async def test_two_factor(svc: Services, cleanup: Cleanup, totp: tb.Totp) -> None:
    cleanup.append(("tfa", tb.SSHD, None))
    await totp.afresh(min_remaining=10.0)
    prompter = FakePrompter(kbdint=kbdint_answers(pw("tfa"), totp))
    _, outcome, io = await run_ok(svc, start("tfa"), prompter)
    assert outcome == "installed"
    assert prompter.kinds() == ["kbdint", "kbdint"]
    texts = [c.detail["prompts"][0][0] for c in prompter.calls]
    assert texts == list(tb.TFA_PROMPTS)
    assert ("verify", "ok", None) in io.trail()
    text, _ = await remote_keys("tfa", key="id_ed25519")
    assert text == installed_line(svc) + "\n"


async def test_fish_login_shell(svc: Services, cleanup: Cleanup) -> None:
    cleanup.append(("fishy", tb.SSHD, None))
    _, outcome, _ = await run_ok(svc, start("fishy"), FakePrompter(password=pw("fishy")))
    assert outcome == "installed"
    assert await remote_keys("fishy") == (installed_line(svc) + "\n", "700 600")


async def test_bad_permissions_then_fix(svc: Services, cleanup: Cleanup) -> None:
    cleanup.append(("badperms", tb.SSHD, None))
    prompter = FakePrompter(password=pw("badperms"))
    _, outcome, io = await run_ok(svc, start("badperms"), prompter, ["fix_permissions"])
    assert outcome == "installed"
    assert ("install", "warn", "perms_warning") in io.trail()
    [asked] = io.asked
    assert asked.code == "key_still_refused"
    assert asked.actions == ["fix_permissions", "save_password_only", "cancel"]
    assert io.trail()[-7:-3] == [
        ("verify", "running", None),
        ("verify", "fail", "key_still_refused"),
        ("verify", "running", None),
        ("verify", "ok", None),
    ]
    text, modes = await remote_keys("badperms")
    assert text == installed_line(svc) + "\n"
    assert modes == "755 600"


async def test_no_shell(svc: Services) -> None:
    prompter = FakePrompter(password=pw("nosh"))
    exc, io = await run_err(svc, start("nosh"), prompter, ["cancel"])
    assert isinstance(exc, PromptCancelled)
    [asked] = io.asked
    assert asked.code == "non_posix"
    assert asked.actions == ["check_key", "cancel"]
    assert asked.output == tb.NOLOGIN_MESSAGE
    assert ("install", "fail", "non_posix") in io.trail()
