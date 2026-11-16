"""Host-key decisions against real servers: first seen, changed, revoked and known_hosts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from portkey.services import Services
from portkey.ssh.chain import OpenChain
from portkey.ssh.errors import CloseCode, HostKeyChanged, HostKeyRejected, HostKeyRevoked
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter
from tests.integration.ssh.helpers import add_conn, open_chain, whoami

KEY_A, KEY_B = tb.hostkey_public("a"), tb.hostkey_public("b")
FP_A, FP_B = tb.hostkey_fingerprint("a"), tb.hostkey_fingerprint("b")

KnownHostsWriter = Callable[[list[str]], None]


def stored_fps(svc: Services, host: str = "sshd-rekeyed", port: int = 22) -> list[str]:
    return [r.fp_sha256 for r in svc.host_keys.for_host(host, port)]


async def test_first_seen_key_declined_sends_nothing_and_stores_nothing(svc: Services) -> None:
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter(hostkey_new=False)
    with pytest.raises(HostKeyRejected) as rejected:
        await open_chain(svc, conn.id, prompter)
    assert rejected.value.close_code == CloseCode.NORMAL
    assert prompter.kinds() == ["hostkey_new"]
    assert prompter.calls[0].detail == {"key_type": "ssh-ed25519", "fingerprint": FP_B}
    assert prompter.phases() == [(0, "connecting"), (0, "hostkey")]  # no auth phase
    assert stored_fps(svc) == []


async def test_first_seen_key_accepted_is_stored_and_trusted_next_time(
    svc: Services, chains: list[OpenChain]
) -> None:
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter(hostkey_new=True)
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert stored_fps(svc) == [FP_B]
    event = svc.events.recent(1)[0]
    assert (event.kind, event.connection_id) == ("hostkey_accepted", conn.id)
    assert event.detail == {"host": "sshd-rekeyed", "port": 22, "fp": FP_B}
    again = FakePrompter()
    chains.append(await open_chain(svc, conn.id, again))
    assert again.kinds() == []


async def test_changed_key_declined_closes_with_4409(svc: Services) -> None:
    svc.host_keys.add("sshd-rekeyed", 22, KEY_A)
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter(hostkey_changed=(False, ""))
    with pytest.raises(HostKeyChanged) as changed:
        await open_chain(svc, conn.id, prompter)
    assert changed.value.close_code == CloseCode.HOSTKEY_CHANGED == 4409
    detail = prompter.calls[0].detail
    assert (detail["old_fingerprint"], detail["new_fingerprint"]) == (FP_A, FP_B)
    assert detail["first_seen_at"] is not None
    assert stored_fps(svc) == [FP_A]


async def test_changed_key_with_the_wrong_name_is_not_replaced(svc: Services) -> None:
    svc.host_keys.add("sshd-rekeyed", 22, KEY_A)
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    with pytest.raises(HostKeyChanged):
        await open_chain(svc, conn.id, FakePrompter(hostkey_changed=(True, "rekeyed")))
    assert stored_fps(svc) == [FP_A]


async def test_changed_key_confirmed_by_name_is_replaced(
    svc: Services, chains: list[OpenChain]
) -> None:
    svc.host_keys.add("sshd-rekeyed", 22, KEY_A)
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter(hostkey_changed=(True, "  REKEYED-Box "))
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "keyonly"
    assert stored_fps(svc) == [FP_B]
    event = svc.events.recent(1)[0]
    assert event.kind == "hostkey_replaced"
    assert (event.detail["old_fp"], event.detail["new_fp"]) == (FP_A, FP_B)


async def test_changed_against_known_hosts_adds_to_the_store_only(
    svc: Services,
    chains: list[OpenChain],
    write_known_hosts: KnownHostsWriter,
    fixture_ssh_dir: Path,
) -> None:
    write_known_hosts([f"sshd-rekeyed {KEY_A}"])
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter(hostkey_changed=(True, "rekeyed-box"))
    chains.append(await open_chain(svc, conn.id, prompter))
    assert prompter.calls[0].detail["first_seen_at"] is None  # the old key is the owner's
    assert stored_fps(svc) == [FP_B]
    assert (fixture_ssh_dir / "known_hosts").read_text() == f"sshd-rekeyed {KEY_A}\n"


async def test_revoked_key_is_refused_without_a_prompt(
    svc: Services, write_known_hosts: KnownHostsWriter
) -> None:
    write_known_hosts([f"@revoked sshd-rekeyed {KEY_B}"])
    svc.host_keys.add("sshd-rekeyed", 22, KEY_B)  # revoked wins over an app record
    conn = add_conn(svc, "rekeyed-box", "sshd-rekeyed", "keyonly")
    prompter = FakePrompter()
    with pytest.raises(HostKeyRevoked) as revoked:
        await open_chain(svc, conn.id, prompter)
    assert revoked.value.code == "hostkey_revoked"
    assert prompter.kinds() == []


async def test_bracketed_known_hosts_entry_trusts_the_other_port(
    svc: Services, chains: list[OpenChain]
) -> None:
    conn = add_conn(svc, "alt-port", "sshd", "keyonly", port=2222)
    prompter = FakePrompter()
    chains.append(chain := await open_chain(svc, conn.id, prompter))
    assert await whoami(chain.target) == "keyonly"


async def test_a_portless_entry_does_not_vouch_for_another_port(
    svc: Services, chains: list[OpenChain], write_known_hosts: KnownHostsWriter
) -> None:
    # OpenSSH semantics, not asyncssh's fallback to the port-less line.
    write_known_hosts([f"sshd {KEY_A}"])
    conn = add_conn(svc, "alt-port", "sshd", "keyonly", port=2222)
    prompter = FakePrompter(hostkey_new=True)
    chains.append(await open_chain(svc, conn.id, prompter))
    assert prompter.kinds() == ["hostkey_new"]
    assert stored_fps(svc, "sshd", 2222) == [FP_A]
