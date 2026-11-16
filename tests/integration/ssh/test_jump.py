"""Jump chains: sshd-inner is reachable only through sshd."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from portkey.models import HopRef, HopTarget
from portkey.services import Services
from portkey.ssh.chain import OpenChain
from portkey.ssh.errors import HostKeyChanged, JumpFailed
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter
from tests.integration.ssh.helpers import (
    BlockingPrompter,
    add_conn,
    established_to,
    open_chain,
    wait_for_established,
    whoami,
)

KnownHostsWriter = Callable[[list[str]], None]


async def test_inner_host_through_the_jump(svc: Services, chains: list[OpenChain]) -> None:
    jump = add_conn(svc, "jump", "sshd", "keyonly")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    prompter = FakePrompter()  # both hosts are in the fixture known_hosts (inner hashed)
    chains.append(chain := await open_chain(svc, inner.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    host = await chain.target.run("hostname", check=True)
    assert str(host.stdout).strip() != ""
    assert prompter.phases() == [
        (0, "connecting"),
        (0, "hostkey"),
        (0, "auth"),
        (1, "connecting"),
        (1, "hostkey"),
        (1, "auth"),
        (1, "ready"),
    ]
    assert [(h.index, h.count, h.role, h.name) for h in chain.hops] == [
        (0, 2, "jump", "jump"),
        (1, 2, "target", "inner"),
    ]


async def test_inner_hop_gets_its_own_host_key_prompt(
    svc: Services, chains: list[OpenChain], write_known_hosts: KnownHostsWriter
) -> None:
    write_known_hosts([f"sshd {tb.hostkey_public('a')}"])  # inner is unknown now
    jump = add_conn(svc, "jump", "sshd", "keyonly")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    prompter = FakePrompter(hostkey_new=True)
    chains.append(chain := await open_chain(svc, inner.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert prompter.kinds() == ["hostkey_new"]
    hop = prompter.calls[0].hop
    assert (hop.index, hop.role, hop.name, hop.host) == (1, "target", "inner", "sshd-inner")
    assert [r.fp_sha256 for r in svc.host_keys.for_host("sshd-inner", 22)] == [
        tb.hostkey_fingerprint("a")
    ]


async def test_each_hop_signs_in_with_its_own_settings(
    svc: Services, chains: list[OpenChain]
) -> None:
    jump = add_conn(svc, "jump", "sshd", "pw", auth_method="password")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    prompter = FakePrompter(password=tb.USERS["pw"].password)
    chains.append(chain := await open_chain(svc, inner.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert prompter.kinds() == ["password"]
    assert prompter.calls[0].hop.index == 0


async def test_a_refused_jump_is_wrapped_in_jump_failed(svc: Services) -> None:
    jump = add_conn(svc, "jump", "sshd", "keyonly", port=2200)  # nothing listens there
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    with pytest.raises(JumpFailed) as failed:
        await open_chain(svc, inner.id, FakePrompter())
    assert failed.value.code == "jump_failed"
    assert failed.value.extra["cause"] == "refused"
    assert failed.value.hop is not None
    assert failed.value.hop.index == 0
    assert "refused the connection on port 2200" in failed.value.message


async def test_a_jump_sign_in_failure_is_wrapped(svc: Services) -> None:
    jump = add_conn(svc, "jump", "sshd", "keyonly", auth_method="password")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    with pytest.raises(JumpFailed) as failed:
        await open_chain(svc, inner.id, FakePrompter())
    assert failed.value.extra["cause"] == "password_not_allowed"


async def test_a_changed_jump_key_keeps_its_own_code(svc: Services) -> None:
    svc.host_keys.add("sshd-rekeyed", 22, tb.hostkey_public("a"))
    jump = add_conn(svc, "jump", "sshd-rekeyed", "keyonly")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    with pytest.raises(HostKeyChanged) as changed:
        await open_chain(svc, inner.id, FakePrompter(hostkey_changed=(False, "")))
    assert changed.value.hop is not None
    assert changed.value.hop.role == "jump"


async def test_close_closes_every_hop_target_first(svc: Services) -> None:
    before = established_to(22)
    jump = add_conn(svc, "jump", "sshd", "keyonly")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    chain = await open_chain(svc, inner.id, FakePrompter())
    assert established_to(22) == before + 1  # the inner hop runs inside the jump's channel
    await chain.close()
    await chain.close()  # idempotent
    assert await wait_for_established(22, before) == before


async def test_cancelling_during_the_inner_prompt_closes_the_jump(
    svc: Services, write_known_hosts: KnownHostsWriter
) -> None:
    write_known_hosts([f"sshd {tb.hostkey_public('a')}"])
    before = established_to(22)
    jump = add_conn(svc, "jump", "sshd", "keyonly")
    inner = add_conn(svc, "inner", "sshd-inner", "keyonly", jump=jump)
    prompter = BlockingPrompter("hostkey_new")
    task = asyncio.create_task(open_chain(svc, inner.id, prompter))
    await asyncio.wait_for(prompter.asked.wait(), 15)
    assert prompter.blocked_hop is not None
    assert prompter.blocked_hop.index == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_for_established(22, before) == before
    assert svc.host_keys.for_host("sshd-inner", 22) == []


async def test_wizard_target_after_a_saved_jump(svc: Services, chains: list[OpenChain]) -> None:
    jump = add_conn(svc, "jump", "sshd", "keyonly")
    draft = HopTarget(
        ref=HopRef(0, 1, None, "new-inner", "sshd-inner", 22, "target"),
        username="keyonly",
        auth_method="key",
        key_fp=None,  # the default key
    )
    hops = svc.connector.plan_new(jump.id, draft)
    assert [(h.ref.index, h.ref.count, h.ref.role) for h in hops] == [
        (0, 2, "jump"),
        (1, 2, "target"),
    ]
    assert hops[1].key_fp is not None
    prompter = FakePrompter()
    chains.append(chain := await svc.connector.connect(hops, prompter))
    assert await whoami(chain.target) == "keyonly"
