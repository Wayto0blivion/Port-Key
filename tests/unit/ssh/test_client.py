"""The auth callbacks of ``PortKeySSHClient``, driven directly (no server)."""

from __future__ import annotations

from typing import Any

import asyncssh
import pytest

from portkey.models import HopTarget
from portkey.ssh.client import AUTH_ORDER, PortKeySSHClient, ssh_options
from portkey.ssh.errors import (
    AuthFailed,
    KeyLocked,
    KeyUnreadable,
    PasswordExpired,
    PasswordNotAllowed,
    PromptCancelled,
    PromptTimeout,
)
from tests.fixtures.prompter import CANCEL, FakePrompter
from tests.unit.ssh.fakes import FakeKeys, KwPrompter, hop, target

HOP = hop(name="web-prod")
PASSPHRASE = "correct horse battery"  # a test-only passphrase


def client(
    hop_target: HopTarget, prompter: Any, keys: FakeKeys | None = None, **kw: Any
) -> PortKeySSHClient:
    keys = keys or FakeKeys()
    return PortKeySSHClient(
        hop_target,
        prompter,
        keys,
        None,  # type: ignore[arg-type]  # host keys are checked before this client runs
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        3600.0,
        vault=keys.vault,
        **kw,
    )


async def aborted(coro: Any, c: PortKeySSHClient) -> Exception:
    """Await an auth callback that must abort; return the stored failure."""
    with pytest.raises(asyncssh.DisconnectError):
        await coro
    assert c.failure is not None
    return c.failure


# --- public key ---------------------------------------------------------------------------------


async def test_plain_key_is_offered_once_with_an_auth_status() -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519")
    prompter = FakePrompter()
    c = client(target(HOP, key_fp=rec.fp), prompter, keys)
    offered = await c.public_key_auth_requested()
    assert offered == [keys.private[rec.fp]]
    assert await c.public_key_auth_requested() is None  # refused once: not again
    assert prompter.phases() == [(0, "auth")]
    assert prompter.statuses[0].detail == {"method": "publickey", "key_name": "id_ed25519"}
    assert c.methods() == ["publickey"]


@pytest.mark.parametrize("remember", [True, False])
async def test_passphrase_retry_and_remember(remember: bool) -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519", passphrase=PASSPHRASE)
    prompter = FakePrompter(passphrase=["wrong", (PASSPHRASE, remember)])
    c = client(target(HOP, key_fp=rec.fp), prompter, keys)
    assert await c.public_key_auth_requested() == [keys.private[rec.fp]]
    assert [call.detail["retry"] for call in prompter.calls] == [False, True]
    assert prompter.calls[0].detail["key"] == rec
    assert keys.vault.unlocked() == ([rec.fp] if remember else [])


async def test_a_vault_key_needs_no_passphrase() -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519", passphrase=PASSPHRASE)
    keys.vault.put(rec.fp, keys.private[rec.fp], None)
    prompter = FakePrompter()
    c = client(target(HOP, key_fp=rec.fp), prompter, keys)
    assert await c.public_key_auth_requested() == [keys.private[rec.fp]]
    assert prompter.kinds() == []


async def test_three_wrong_passphrases_are_key_locked() -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519", passphrase=PASSPHRASE)
    c = client(target(HOP, key_fp=rec.fp), FakePrompter(passphrase=["a", "b", "c"]), keys)
    failure = await aborted(c.public_key_auth_requested(), c)
    assert isinstance(failure, KeyLocked)
    assert failure.hop == HOP
    assert "id_ed25519" in failure.message
    assert "3 tries" in failure.message


async def test_cancelled_passphrase_prompt() -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519", passphrase=PASSPHRASE)
    c = client(target(HOP, key_fp=rec.fp), FakePrompter(passphrase=CANCEL), keys)
    assert isinstance(await aborted(c.public_key_auth_requested(), c), PromptCancelled)


async def test_a_missing_key_file_is_key_unreadable() -> None:
    c = client(target(HOP, key_fp="SHA256:" + "B" * 43), FakePrompter())
    failure = await aborted(c.public_key_auth_requested(), c)
    assert isinstance(failure, KeyUnreadable)
    assert failure.hop == HOP


@pytest.mark.parametrize(
    "hop_target", [target(HOP, allow_key=False), target(HOP, auth_method="password", key_fp=None)]
)
async def test_key_not_allowed_is_declined_but_recorded(hop_target: HopTarget) -> None:
    c = client(hop_target, FakePrompter())
    assert await c.public_key_auth_requested() is None
    assert c.methods() == ["publickey"]


# --- password -----------------------------------------------------------------------------------


async def test_password_retries_then_auth_failed() -> None:
    prompter = FakePrompter(password=["a", "b", "c"])
    c = client(target(HOP), prompter)
    assert [await c.password_auth_requested() for _ in range(3)] == ["a", "b", "c"]
    assert [call.detail["retry"] for call in prompter.calls] == [False, True, True]
    assert [call.detail["user"] for call in prompter.calls] == ["john"] * 3
    failure = await aborted(c.password_auth_requested(), c)
    assert isinstance(failure, AuthFailed)
    assert failure.methods == ["password"]
    assert "rejected the password for john" in failure.message
    assert prompter.phases() == [(0, "auth")]  # announced once


async def test_password_not_allowed_is_declined() -> None:
    prompter = FakePrompter()
    c = client(target(HOP, allow_password=False), prompter)
    assert await c.password_auth_requested() is None
    assert prompter.kinds() == []


async def test_password_prompt_timeout() -> None:
    def late(**_: Any) -> str:
        raise PromptTimeout()

    c = client(target(HOP), FakePrompter(password=late))
    assert isinstance(await aborted(c.password_auth_requested(), c), PromptTimeout)


async def test_expired_password() -> None:
    c = client(target(HOP), FakePrompter())
    failure = await aborted(c.password_change_requested("Password expired", "en"), c)
    assert isinstance(failure, PasswordExpired)
    assert failure.code == "password_expired"


# --- keyboard-interactive -----------------------------------------------------------------------


async def test_kbdint_rounds_relayed_and_retry_marked() -> None:
    prompter = KwPrompter(kbdint=[["pw"], ["123456"], ["pw"]])
    c = client(target(HOP), prompter)
    assert await c.kbdint_auth_requested() == ""
    assert await c.kbdint_challenge_received("", "", "en", [("Password: ", False)]) == ["pw"]
    code = await c.kbdint_challenge_received("", "", "en", [("Verification code: ", False)])
    assert code == ["123456"]
    assert await c.kbdint_auth_requested() == ""  # the server failed the first attempt
    await c.kbdint_challenge_received("", "", "en", [("Password: ", False)])
    assert prompter.extras == [{"retry": False}, {"retry": False}, {"retry": True}]
    assert prompter.calls[0].detail["prompts"] == [("Password: ", False)]


async def test_empty_kbdint_round_is_answered_without_a_prompt() -> None:
    prompter = FakePrompter()
    c = client(target(HOP), prompter)
    await c.kbdint_auth_requested()
    assert await c.kbdint_challenge_received("", "", "en", []) == []
    assert prompter.kinds() == []


async def test_kbdint_round_with_only_text_is_shown() -> None:
    prompter = FakePrompter(kbdint=[[]])
    c = client(target(HOP), prompter)
    await c.kbdint_auth_requested()
    assert await c.kbdint_challenge_received("", "Password expires soon", "en", []) == []
    assert prompter.kinds() == ["kbdint"]


async def test_kbdint_attempts_are_limited() -> None:
    c = client(target(HOP), FakePrompter())
    for _ in range(3):
        await c.kbdint_auth_requested()
    failure = await aborted(c.kbdint_auth_requested(), c)
    assert isinstance(failure, AuthFailed)
    assert failure.methods == ["keyboard-interactive"]


async def test_kbdint_not_allowed_is_declined() -> None:
    c = client(target(HOP, allow_kbdint=False), FakePrompter())
    assert await c.kbdint_auth_requested() is None
    assert c.methods() == ["keyboard-interactive"]


# --- what a failed sign-in reports --------------------------------------------------------------


async def test_refused_key_reports_auth_failed_with_methods() -> None:
    keys = FakeKeys()
    rec = keys.add("id_ed25519")
    c = client(target(HOP, key_fp=rec.fp, allow_password=False), FakePrompter(), keys)
    await c.public_key_auth_requested()
    await c.password_auth_requested()  # offered but not allowed this time
    error = c.outcome_error()
    assert isinstance(error, AuthFailed)
    assert error.message == "web-prod refused your key."
    assert error.methods == ["publickey", "password"]
    assert error.hop == HOP


async def test_password_wanted_but_not_offered_is_password_not_allowed() -> None:
    c = client(target(HOP, auth_method="password", key_fp=None, allow_key=False), FakePrompter())
    await c.public_key_auth_requested()
    error = c.outcome_error()
    assert isinstance(error, PasswordNotAllowed)
    assert error.extra["methods"] == ["publickey"]
    assert error.message.startswith("web-prod does not accept passwords.")


async def test_key_planned_but_publickey_not_offered() -> None:
    c = client(target(HOP), FakePrompter())
    error = c.outcome_error()
    assert isinstance(error, AuthFailed)
    assert "does not accept key logins" in error.message


async def test_failure_keeps_the_first_cause() -> None:
    c = client(target(HOP), FakePrompter(password=CANCEL, passphrase=CANCEL))
    first = await aborted(c.password_auth_requested(), c)
    assert isinstance(first, PromptCancelled)
    c.max_attempts = 0  # the next password request now fails on its own
    await aborted(c.password_auth_requested(), c)
    assert c.failure is first


def test_ssh_options_load_nothing_from_the_container() -> None:
    opts = ssh_options(target(hop(port=2222)), 10.0, 30.0, 3)
    assert opts["port"] == 2222
    assert opts["username"] == "john"
    for name in ("config", "client_keys", "agent_path", "password", "x509_trusted_certs"):
        assert opts[name] is None, name
    assert opts["preferred_auth"] == list(AUTH_ORDER)
    assert (opts["keepalive_interval"], opts["keepalive_count_max"]) == (30.0, 3)
    assert opts["connect_timeout"] is None
    assert opts["login_timeout"] == 10.0 + 120.0 * 5
