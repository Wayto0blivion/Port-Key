"""Signing in through the real ``ChainConnector``: keys, passphrases, passwords and 2FA."""

from __future__ import annotations

import asyncio

import pytest

from portkey.services import Services
from portkey.ssh.chain import OpenChain
from portkey.ssh.errors import (
    AuthFailed,
    KeyLocked,
    PasswordNotAllowed,
    PromptCancelled,
)
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import CANCEL, FakePrompter, kbdint_answers
from tests.integration.ssh.helpers import (
    BlockingPrompter,
    add_conn,
    established_to,
    key_fp,
    open_chain,
    wait_for_established,
    whoami,
)


async def test_key_login_with_the_default_key(svc: Services, chains: list[OpenChain]) -> None:
    conn = add_conn(svc, "box", "sshd", "keyonly")
    prompter = FakePrompter()  # any prompt at all fails the test
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert prompter.phases() == [(0, "connecting"), (0, "hostkey"), (0, "auth"), (0, "ready")]
    assert prompter.statuses[1].detail == {"source": "known_hosts"}  # sshd is in known_hosts
    assert prompter.statuses[2].detail == {"method": "publickey", "key_name": "id_ed25519"}
    assert [h.role for h in chain.hops] == ["target"]
    assert chain.hops[0].connection_id == conn.id


async def test_password_fallback_after_a_wrong_password(
    svc: Services, chains: list[OpenChain]
) -> None:
    conn = add_conn(svc, "pw-box", "sshd", "pw")  # a key connection; pw only takes passwords
    prompter = FakePrompter(password=["not the password", tb.USERS["pw"].password])
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "pw"
    assert prompter.kinds() == ["password", "password"]
    assert [c.detail["retry"] for c in prompter.calls] == [False, True]
    assert prompter.calls[0].detail["user"] == "pw"
    methods = [s.detail.get("method") for s in prompter.statuses if s.phase == "auth"]
    assert methods == ["password"]


async def test_three_wrong_passwords_fail_with_the_methods_offered(svc: Services) -> None:
    conn = add_conn(svc, "pw-box", "sshd", "pw", auth_method="password")
    prompter = FakePrompter(password=["wrong 1", "wrong 2", "wrong 3"])
    with pytest.raises(AuthFailed) as failed:
        await open_chain(svc, conn.id, prompter)
    assert failed.value.methods == ["password"]
    assert failed.value.hop is not None
    assert failed.value.hop.name == "pw-box"
    assert "rejected the password for pw" in failed.value.message
    assert prompter.kinds() == ["password"] * 3


async def test_totp_in_two_keyboard_interactive_rounds(
    svc: Services, chains: list[OpenChain], totp: tb.Totp
) -> None:
    conn = add_conn(svc, "tfa-box", "sshd", "tfa")  # the default key is not on tfa
    await totp.afresh(min_remaining=20.0)
    prompter = FakePrompter(kbdint=kbdint_answers(tb.USERS["tfa"].password, totp))
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "tfa"
    assert prompter.kinds() == ["kbdint", "kbdint"]
    texts = [c.detail["prompts"][0][0] for c in prompter.calls]
    assert tuple(texts) == tb.TFA_PROMPTS
    assert all(c.detail["prompts"][0][1] is False for c in prompter.calls)  # no echo
    methods = [s.detail.get("method") for s in prompter.statuses if s.phase == "auth"]
    assert methods == ["publickey", "keyboard-interactive"]


@pytest.mark.parametrize("name", ["id_ed25519_pass", "id_rsa_pass"])
async def test_encrypted_key_asks_again_after_a_wrong_passphrase_and_remembers(
    svc: Services, chains: list[OpenChain], name: str
) -> None:
    conn = add_conn(svc, "locked-key", "sshd", "keyonly", key=name)
    prompter = FakePrompter(passphrase=["not it", (tb.KEY_PASSPHRASE, True)])
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert prompter.kinds() == ["passphrase", "passphrase"]
    assert [c.detail["retry"] for c in prompter.calls] == [False, True]
    assert prompter.calls[0].detail["key"].name == name
    assert svc.vault.unlocked() == [key_fp(name)]
    # Remembered: the next connection needs no prompt.
    again = FakePrompter()
    chains.append(await open_chain(svc, conn.id, again))
    assert again.kinds() == []


async def test_passphrase_without_remember_is_used_once(
    svc: Services, chains: list[OpenChain]
) -> None:
    conn = add_conn(svc, "locked-key", "sshd", "keyonly", key="id_ed25519_pass")
    prompter = FakePrompter(passphrase=(tb.KEY_PASSPHRASE, False))
    chains.append(await open_chain(svc, conn.id, prompter))
    assert svc.vault.unlocked() == []


async def test_three_wrong_passphrases_lock_the_attempt(svc: Services) -> None:
    conn = add_conn(svc, "locked-key", "sshd", "keyonly", key="id_ed25519_pass")
    prompter = FakePrompter(passphrase=["a", "b", "c"])
    with pytest.raises(KeyLocked) as locked:
        await open_chain(svc, conn.id, prompter)
    assert locked.value.code == "key_locked"
    assert locked.value.hop is not None
    assert locked.value.hop.index == 0
    assert prompter.kinds() == ["passphrase"] * 3


async def test_keyonly_refuses_a_password_connection(svc: Services) -> None:
    conn = add_conn(svc, "keys-only", "sshd", "keyonly", auth_method="password")
    prompter = FakePrompter()
    with pytest.raises(PasswordNotAllowed) as refused:
        await open_chain(svc, conn.id, prompter)
    assert refused.value.code == "password_not_allowed"
    assert refused.value.extra["methods"] == ["publickey"]
    assert prompter.kinds() == []


async def test_sign_in_with_password_this_time_on_keyonly(svc: Services) -> None:
    conn = add_conn(svc, "keys-only", "sshd", "keyonly")
    with pytest.raises(PasswordNotAllowed):
        await open_chain(svc, conn.id, FakePrompter(), force_password=True)


async def test_an_unknown_key_is_refused_with_auth_failed(svc: Services) -> None:
    conn = add_conn(svc, "keys-only", "sshd", "keyonly", key="id_ed25519_portkey")
    with pytest.raises(AuthFailed) as refused:
        await open_chain(svc, conn.id, FakePrompter())
    assert refused.value.methods == ["publickey"]
    assert refused.value.message == "keys-only refused your key."


async def test_cancelling_the_password_prompt_ends_the_attempt(svc: Services) -> None:
    conn = add_conn(svc, "pw-box", "sshd", "pw", auth_method="password")
    with pytest.raises(PromptCancelled):
        await open_chain(svc, conn.id, FakePrompter(password=CANCEL))


async def test_cancelling_the_task_while_a_prompt_waits_leaves_nothing_connected(
    svc: Services,
) -> None:
    before = established_to(22)
    conn = add_conn(svc, "pw-box", "sshd", "pw", auth_method="password")
    prompter = BlockingPrompter("password")
    task = asyncio.create_task(open_chain(svc, conn.id, prompter))
    await asyncio.wait_for(prompter.asked.wait(), 15)
    assert established_to(22) == before + 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_for_established(22, before) == before
