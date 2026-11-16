"""The install flow's state machine against a fake SSH layer and a real SQLite store."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from portkey.models import KnownHostsMatch
from portkey.ssh.errors import BadMessage, PromptCancelled
from portkey.store.hostkeys import SqliteHostKeyStore
from portkey.wizard.remote_script import FIX_PERMS_SCRIPT
from tests.unit.wizard.fakes import FakeConn, key_refused, key_works, password_works
from tests.unit.wizard.world import PASSWORD, World, run, run_fails, start

CONNECT = [
    ("validate", "running", None),
    ("validate", "ok", None),
    ("connect", "running", None),
    ("connect", "ok", None),
    ("hostkey", "running", None),
    ("hostkey", "ok", "known"),
]
INSTALLED_TAIL = [
    ("verify", "running", None),
    ("verify", "ok", None),
    ("discard_password", "ok", None),
    ("save", "running", None),
    ("save", "ok", None),
]


# --- the main paths ---------------------------------------------------------------------------


async def test_key_already_works(world: Callable[..., World]) -> None:
    w = world(key_works())
    record, outcome, io, prompter = await run(w)
    assert outcome == "already_installed"
    assert io.trail() == [
        *CONNECT,
        ("try_key", "running", None),
        ("try_key", "ok", "already_installed"),
        ("save", "running", None),
        ("save", "ok", None),
    ]
    assert prompter.calls == []
    assert w.connector.chains[0].closed
    assert w.connector.chains[0].target.commands == []  # no script ran
    saved = w.connections.get(record.id)
    assert (saved.name, saved.auth_method, saved.key_fp, saved.tags) == (
        "web-prod",
        "key",
        None,
        ("prod",),
    )
    assert (saved.color, saved.notes) == ("red", "rack 3")
    [event] = w.events()
    assert event.kind == "key_installed"
    assert event.detail == {
        "host": "192.168.10.42",
        "port": 22,
        "user": "john",
        "fp": w.key.fp,
        "outcome": "already_installed",
    }
    first = w.connector.connects[0][-1]
    assert (first.allow_key, first.allow_password, first.allow_kbdint) == (True, True, True)
    assert first.key_fp == w.key.fp
    assert first.ref.connection_id is None


async def test_full_path_with_a_password(world: Callable[..., World]) -> None:
    conn = FakeConn()
    w = world(password_works(conn), key_works())
    record, outcome, io, prompter = await run(w)
    assert outcome == "installed"
    assert io.trail() == [
        *CONNECT,
        ("try_key", "running", None),
        ("try_key", "warn", "refused"),
        ("password_login", "running", None),
        ("password_login", "ok", None),
        ("install", "running", None),
        ("install", "ok", "added"),
        *INSTALLED_TAIL,
    ]
    assert prompter.kinds() == ["password"]
    [(command, script)] = conn.commands
    assert command == "sh -s"
    assert script is not None
    assert w.key.public_key in script
    verify = w.connector.connects[1][-1]
    assert (verify.allow_key, verify.allow_password, verify.allow_kbdint) == (True, False, False)
    assert w.connector.chains[1].target.commands == [("echo portkey-ok", None)]
    tl = w.connector.timeline
    assert (
        tl.index("close target")
        < tl.index("step discard_password ok")
        < tl.index("step save running")
    )
    assert all(c.closed for c in w.connector.chains)
    assert w.connections.get(record.id).auth_method == "key"
    assert [e.detail["outcome"] for e in w.events()] == ["installed"]


async def test_two_factor_is_relayed(world: Callable[..., World]) -> None:
    w = world(password_works(kbdint=True), key_works())
    _, outcome, io, prompter = await run(w)
    assert outcome == "installed"
    assert prompter.kinds() == ["kbdint"]
    assert ("password_login", "ok", None) in io.trail()


async def test_a_prompted_host_key_reports_accepted(world: Callable[..., World]) -> None:
    w = world(key_works(new_key=True))
    _, _, io, prompter = await run(w)
    assert ("hostkey", "ok", "accepted") in io.trail()
    assert prompter.kinds() == ["hostkey_new"]


async def test_app_store_key_reports_trusted(world: Callable[..., World]) -> None:
    w = world(key_works())
    w.known.match = KnownHostsMatch()
    SqliteHostKeyStore(w.db).add("192.168.10.42", 22, "ssh-ed25519 " + "A" * 68)
    _, _, io, _ = await run(w)
    assert ("hostkey", "ok", "trusted") in io.trail()


@pytest.mark.parametrize(("source", "code"), [("known_hosts", "known"), ("store", "trusted")])
async def test_the_ssh_cores_source_decides_the_hostkey_code(
    world: Callable[..., World], source: str, code: str
) -> None:
    # Both sources hold keys for the host, where the guess from records alone says "trusted";
    # the SSH core's verdict source is exact.
    w = world(key_works(source=source))
    SqliteHostKeyStore(w.db).add("192.168.10.42", 22, "ssh-ed25519 " + "A" * 68)
    _, _, io, _ = await run(w)
    assert ("hostkey", "ok", code) in io.trail()


async def test_the_password_goes_nowhere_else(
    world: Callable[..., World], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    w = world(password_works(), key_refused(("publickey", "password")))
    _, outcome, io, _ = await run(w, actions=["save_password_only"])
    assert outcome == "saved_password_only"
    sent = [m.model_dump_json() for m in [*io.steps, *io.asked]]
    assert all(PASSWORD not in text for text in sent)
    assert PASSWORD not in caplog.text
    rows = w.db.query("SELECT * FROM events")
    assert PASSWORD not in repr(rows)


# --- choices ----------------------------------------------------------------------------------


async def test_assume_installed_and_the_key_works(world: Callable[..., World]) -> None:
    w = world(key_works())
    _, outcome, io, _ = await run(w, start(assume_installed=True))
    assert outcome == "already_installed"
    assert io.trail()[-4:] == [
        ("try_key", "ok", "already_installed"),
        ("password_login", "skipped", None),
        ("save", "running", None),
        ("save", "ok", None),
    ]
    first = w.connector.connects[0][-1]
    assert (first.allow_password, first.allow_kbdint) == (False, False)


async def test_assume_installed_refused_then_install_with_password(
    world: Callable[..., World],
) -> None:
    w = world(key_refused(("publickey", "password")), password_works(), key_works())
    _, outcome, io, _ = await run(w, start(assume_installed=True), ["install_with_password"])
    assert outcome == "installed"
    [asked] = io.asked
    assert asked.code == "key_refused"
    assert asked.actions == ["install_with_password", "save_password_only", "cancel"]
    assert asked.public_key == " ".join(w.key.public_key.split())
    assert asked.install_command
    assert asked.install_command.startswith("mkdir -p ~/.ssh")
    second = w.connector.connects[1][-1]
    assert (second.allow_key, second.allow_password, second.auth_method) == (
        False,
        True,
        "password",
    )
    assert ("try_key", "warn", "refused") in io.trail()
    assert ("password_login", "ok", None) in io.trail()


async def test_assume_installed_refused_then_save_password_only(
    world: Callable[..., World],
) -> None:
    w = world(key_refused(("publickey", "password")))
    record, outcome, io, _ = await run(w, start(assume_installed=True), ["save_password_only"])
    assert outcome == "saved_password_only"
    saved = w.connections.get(record.id)
    assert (saved.auth_method, saved.key_fp) == ("password", None)
    assert w.events() == []
    assert ("discard_password", "ok", None) not in io.trail()


async def test_assume_installed_refused_by_a_key_only_server(world: Callable[..., World]) -> None:
    w = world(key_refused(("publickey",)))
    exc, io = await run_fails(w, start(assume_installed=True), ["cancel"])
    assert isinstance(exc, PromptCancelled)
    assert [a.code for a in io.asked] == ["password_not_allowed"]


async def test_password_not_allowed_then_check_key(world: Callable[..., World]) -> None:
    w = world(key_refused(), key_refused(quiet=True), key_works())
    _, outcome, io, _ = await run(w, actions=["check_key", "check_key"])
    assert outcome == "already_installed"
    assert [a.code for a in io.asked] == ["password_not_allowed", "password_not_allowed"]
    assert io.asked[0].actions == ["check_key", "cancel"]
    assert io.asked[0].install_command
    assert io.trail()[6:] == [
        ("try_key", "running", None),
        ("try_key", "warn", "refused"),
        ("try_key", "running", None),
        ("try_key", "warn", "refused"),
        ("try_key", "running", None),
        ("try_key", "ok", "already_installed"),
        ("save", "running", None),
        ("save", "ok", None),
    ]
    assert all(
        (h[-1].allow_password, h[-1].allow_kbdint) == (False, False)
        for h in [
            w.connector.connects[1],
            w.connector.connects[2],
        ]
    )


async def test_cancel_at_needs_action(world: Callable[..., World]) -> None:
    w = world(key_refused())
    exc, io = await run_fails(w, actions=["cancel"])
    assert isinstance(exc, PromptCancelled)
    assert all(state != "fail" for _, state, _ in io.trail())
    assert w.connections.list_all() == []


async def test_an_action_that_was_not_offered_is_a_bad_message(
    world: Callable[..., World],
) -> None:
    w = world(key_refused())
    exc, _ = await run_fails(w, actions=["fix_permissions"])
    assert isinstance(exc, BadMessage)


def nologin(command: str, stdin: str | None) -> tuple[str, str, int | None]:
    return "This account is currently not available.\n", "", 1


def windows(command: str, stdin: str | None) -> tuple[str, str, int | None]:
    return "", "'sh' is not recognized as an internal or external command,\r\n", 1


async def test_non_posix_then_check_key(world: Callable[..., World]) -> None:
    w = world(password_works(FakeConn(nologin)), key_works())
    _, outcome, io, _ = await run(w, actions=["check_key"])
    assert outcome == "already_installed"
    [asked] = io.asked
    assert (asked.code, asked.actions) == ("non_posix", ["check_key", "cancel"])
    assert asked.output == "This account is currently not available."
    assert "C:\\ProgramData" not in asked.message
    assert ("install", "fail", "non_posix") in io.trail()
    assert io.trail()[-3:] == [
        ("discard_password", "ok", None),
        ("save", "running", None),
        ("save", "ok", None),
    ]


async def test_non_posix_windows_hint(world: Callable[..., World]) -> None:
    w = world(password_works(FakeConn(windows)))
    exc, io = await run_fails(w, actions=["cancel"])
    assert isinstance(exc, PromptCancelled)
    assert "administrators_authorized_keys" in io.asked[0].message
    assert all(c.closed for c in w.connector.chains)


def readonly(command: str, stdin: str | None) -> tuple[str, str, int | None]:
    return (
        "PORTKEY_BEGIN\nPORTKEY_READONLY\n",
        "sh: 1: cannot create /home/john/.ssh/authorized_keys: Read-only file system\n",
        0,
    )


async def test_authorized_keys_readonly(world: Callable[..., World]) -> None:
    w = world(password_works(FakeConn(readonly)))
    record, outcome, io, _ = await run(w, actions=["save_password_only"])
    assert outcome == "saved_password_only"
    [asked] = io.asked
    assert asked.code == "authorized_keys_readonly"
    assert asked.actions == ["save_password_only", "cancel"]
    assert "(Read-only file system)" in asked.message
    assert asked.output
    assert "Read-only file system" in asked.output
    assert ("install", "fail", "authorized_keys_readonly") in io.trail()
    assert w.connections.get(record.id).auth_method == "password"


def loose(command: str, stdin: str | None) -> tuple[str, str, int | None]:
    if stdin == FIX_PERMS_SCRIPT:
        return "", "", 0
    return "PORTKEY_BEGIN\nPORTKEY_ADDED\nPORTKEY_WARN_PERMS\n", "", 0


async def test_key_still_refused_then_fix_permissions(world: Callable[..., World]) -> None:
    conn = FakeConn(loose)
    w = world(password_works(conn), key_refused(quiet=True), key_works())
    _, outcome, io, _ = await run(w, actions=["fix_permissions"])
    assert outcome == "installed"
    [asked] = io.asked
    assert asked.code == "key_still_refused"
    assert asked.actions == ["fix_permissions", "save_password_only", "cancel"]
    assert "Port-Key added your key" in asked.message
    assert [c[1] for c in conn.commands][1] == FIX_PERMS_SCRIPT
    assert ("install", "warn", "perms_warning") in io.trail()
    assert io.trail()[-7:] == [
        ("verify", "running", None),
        ("verify", "fail", "key_still_refused"),
        *INSTALLED_TAIL,
    ]


async def test_still_refused_after_the_fix(world: Callable[..., World]) -> None:
    w = world(password_works(FakeConn(loose)), key_refused(quiet=True), key_refused(quiet=True))
    _, outcome, io, _ = await run(w, actions=["fix_permissions", "save_password_only"])
    assert outcome == "saved_password_only"
    assert io.asked[1].actions == ["save_password_only", "cancel"]
    assert "after Port-Key fixed the permissions" in io.asked[1].message


async def test_server_without_key_logins(world: Callable[..., World]) -> None:
    w = world(password_works(), key_refused(("password",), quiet=True))
    _, _, io, _ = await run(w, actions=["save_password_only"])
    assert io.asked[0].actions == ["save_password_only", "cancel"]
    assert "does not allow key logins" in io.asked[0].message
