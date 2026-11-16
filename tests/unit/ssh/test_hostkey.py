"""The host-key decision table, its prompts and what gets stored."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import asyncssh
import pytest

from portkey.keys.known_hosts import FileKnownHosts
from portkey.models import HostKeyRecord, KnownHostsMatch
from portkey.ssh.errors import (
    CloseCode,
    HostKeyChanged,
    HostKeyRejected,
    HostKeyRevoked,
    PromptCancelled,
)
from portkey.ssh.hostkey import (
    Verdict,
    confirm_name_matches,
    decide,
    fingerprint,
    normalize_key,
    trusted_algorithms,
    verify_host_key,
)
from portkey.store.events import SqliteEventLog
from portkey.store.hostkeys import SqliteHostKeyStore
from tests.fixtures.prompter import CANCEL, FakePrompter
from tests.unit.ssh.fakes import KwPrompter, StubKnownHosts, hop, host_key

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
NEW_KEY = host_key("new")
OLD_KEY = host_key("old")
OLD_ECDSA = host_key("old-ecdsa", "ecdsa-sha2-nistp256")
HOP = hop(name="web-prod", host="web-prod.example.test")


def record(key: str, rid: int = 1, first_seen: datetime = T0) -> HostKeyRecord:
    return HostKeyRecord(
        id=rid,
        host="web-prod.example.test",
        port=22,
        key_type=key.split()[0],
        public_key=key,
        fp_sha256=fingerprint(key),
        first_seen_at=first_seen,
        last_seen_at=first_seen,
    )


# --- fingerprints and names ----------------------------------------------------------------------


def test_fingerprint_is_openssh_sha256() -> None:
    key = asyncssh.import_public_key(NEW_KEY)
    assert fingerprint(NEW_KEY) == key.get_fingerprint("sha256")
    assert fingerprint(NEW_KEY + " root@web-prod") == fingerprint(NEW_KEY)
    assert not fingerprint(NEW_KEY).endswith("=")


def test_normalize_drops_the_comment_and_rejects_junk() -> None:
    assert normalize_key(f"  {NEW_KEY} a comment \n") == NEW_KEY
    for junk in ("", "ssh-ed25519", "ssh-ed25519 not*base64"):
        with pytest.raises(ValueError, match="not an OpenSSH public key"):
            normalize_key(junk)


@pytest.mark.parametrize(
    ("typed", "ok"),
    [
        ("web-prod", True),
        ("  WEB-Prod \t", True),
        ("web-prod2", False),
        ("web prod", False),
        ("", False),
        ("webprod", False),
    ],
)
def test_confirm_name_is_trimmed_and_case_insensitive(typed: str, ok: bool) -> None:
    assert confirm_name_matches(typed, "web-prod") is ok


def test_confirm_name_never_matches_an_empty_expected_name() -> None:
    assert not confirm_name_matches("", "  ")


# --- the decision table -----------------------------------------------------------------------


def test_revoked_wins_over_every_trust() -> None:
    known = KnownHostsMatch(trusted=(NEW_KEY,), revoked=(NEW_KEY + " comment",))
    decision = decide(NEW_KEY, known, [record(NEW_KEY)])
    assert decision.verdict is Verdict.REVOKED


def test_trusted_by_known_hosts() -> None:
    decision = decide(NEW_KEY + " root@web", KnownHostsMatch(trusted=(NEW_KEY,)), [])
    assert decision.verdict is Verdict.TRUSTED
    assert decision.trusted_record is None
    assert decision.fingerprint == fingerprint(NEW_KEY)


def test_trusted_by_the_app_store() -> None:
    rec = record(NEW_KEY)
    decision = decide(NEW_KEY, KnownHostsMatch(), [record(OLD_KEY, 2), rec])
    assert decision.verdict is Verdict.TRUSTED
    assert decision.trusted_record == rec


def test_no_record_at_all_is_new() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(), [])
    assert (decision.verdict, decision.key_type) == (Verdict.NEW, "ssh-ed25519")


def test_only_a_revoked_record_is_still_new() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(revoked=(OLD_KEY,)), [record(OLD_KEY)])
    assert decision.verdict is Verdict.NEW


def test_changed_against_the_app_store_knows_first_seen() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(), [record(OLD_KEY)])
    assert decision.verdict is Verdict.CHANGED
    assert decision.old_fingerprint == fingerprint(OLD_KEY)
    assert decision.old_key_type == "ssh-ed25519"
    assert decision.old_first_seen_at == T0


def test_changed_against_known_hosts_has_no_first_seen() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(trusted=(OLD_KEY,)), [])
    assert decision.verdict is Verdict.CHANGED
    assert decision.old_fingerprint == fingerprint(OLD_KEY)
    assert decision.old_first_seen_at is None


def test_changed_prefers_an_old_key_of_the_same_type() -> None:
    known = KnownHostsMatch(trusted=(OLD_ECDSA, OLD_KEY))
    decision = decide(NEW_KEY, known, [record(OLD_ECDSA)])
    assert (decision.old_key_type, decision.old_fingerprint) == (
        "ssh-ed25519",
        fingerprint(OLD_KEY),
    )


def test_changed_falls_back_to_another_type_with_its_type() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(), [record(OLD_ECDSA)])
    assert decision.old_key_type == "ecdsa-sha2-nistp256"


def test_trusted_algorithms_follow_the_trusted_keys() -> None:
    algs = trusted_algorithms(KnownHostsMatch(trusted=(OLD_ECDSA,)), [record(OLD_KEY)])
    assert algs == ["ecdsa-sha2-nistp256", "ssh-ed25519"]
    assert trusted_algorithms(KnownHostsMatch(), []) == []
    revoked = KnownHostsMatch(trusted=(OLD_ECDSA,), revoked=(OLD_ECDSA,))
    assert trusted_algorithms(revoked, []) == []


# --- verify_host_key: prompts, storage, events -----------------------------------------------


async def verify(
    prompter: FakePrompter,
    store: SqliteHostKeyStore,
    events: SqliteEventLog,
    known: KnownHostsMatch | None = None,
    key: str = NEW_KEY,
) -> None:
    await verify_host_key(
        HOP, key, prompter, store, StubKnownHosts(known or KnownHostsMatch()), events
    )


async def test_new_key_accepted_is_stored_with_an_event(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    prompter = FakePrompter(hostkey_new=True)
    await verify(prompter, store, events)
    assert prompter.calls[0].detail == {
        "key_type": "ssh-ed25519",
        "fingerprint": fingerprint(NEW_KEY),
    }
    assert [r.public_key for r in store.for_host(HOP.host, 22)] == [NEW_KEY]
    event = events.recent(1)[0]
    assert (event.kind, event.connection_id) == ("hostkey_accepted", HOP.connection_id)
    assert event.detail == {"host": HOP.host, "port": 22, "fp": fingerprint(NEW_KEY)}


async def test_new_key_declined_stores_nothing(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    with pytest.raises(HostKeyRejected) as rejected:
        await verify(FakePrompter(hostkey_new=False), store, events)
    assert rejected.value.close_code == CloseCode.NORMAL
    assert rejected.value.hop == HOP
    assert store.for_host(HOP.host, 22) == []
    assert events.recent() == []


async def test_new_key_prompt_cancelled(store: SqliteHostKeyStore, events: SqliteEventLog) -> None:
    with pytest.raises(PromptCancelled):
        await verify(FakePrompter(hostkey_new=CANCEL), store, events)
    assert store.for_host(HOP.host, 22) == []


async def test_trusted_store_key_is_touched_without_a_prompt(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    rec = store.add(HOP.host, 22, NEW_KEY)
    store.db.execute("UPDATE host_keys SET last_seen_at = ?", ("2026-01-01T00:00:00Z",))
    prompter = FakePrompter()
    await verify(prompter, store, events)
    assert prompter.kinds() == []
    assert store.get(rec.id).last_seen_at > datetime(2026, 1, 1, tzinfo=UTC)


async def test_known_hosts_key_needs_no_prompt_and_stores_nothing(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    prompter = FakePrompter()
    await verify(prompter, store, events, KnownHostsMatch(trusted=(NEW_KEY,)))
    assert prompter.kinds() == []
    assert store.for_host(HOP.host, 22) == []


async def test_revoked_key_is_refused_without_a_prompt(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    prompter = FakePrompter()
    with pytest.raises(HostKeyRevoked) as revoked:
        await verify(prompter, store, events, KnownHostsMatch(revoked=(NEW_KEY,)))
    assert revoked.value.close_code == CloseCode.SSH_FAILURE
    assert prompter.kinds() == []


@pytest.mark.parametrize("reply", [(False, "web-prod"), (True, "web-prod2"), (True, "")])
async def test_changed_key_not_replaced_closes_4409(
    store: SqliteHostKeyStore, events: SqliteEventLog, reply: tuple[bool, str]
) -> None:
    store.add(HOP.host, 22, OLD_KEY)
    with pytest.raises(HostKeyChanged) as changed:
        await verify(FakePrompter(hostkey_changed=reply), store, events)
    assert changed.value.close_code == CloseCode.HOSTKEY_CHANGED
    assert "sent no password and no key" in changed.value.message
    assert [r.public_key for r in store.for_host(HOP.host, 22)] == [OLD_KEY]
    assert events.recent() == []


async def test_changed_key_replaced_by_typed_name(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    old = store.add(HOP.host, 22, OLD_KEY)
    prompter = FakePrompter(hostkey_changed=(True, " Web-Prod "))
    await verify(prompter, store, events)
    detail = prompter.calls[0].detail
    assert detail["old_fingerprint"] == fingerprint(OLD_KEY)
    assert detail["new_fingerprint"] == fingerprint(NEW_KEY)
    assert detail["first_seen_at"] == old.first_seen_at
    assert [r.public_key for r in store.for_host(HOP.host, 22)] == [NEW_KEY]
    event = events.recent(1)[0]
    assert event.kind == "hostkey_replaced"
    assert event.detail["old_fp"] == fingerprint(OLD_KEY)
    assert event.detail["new_fp"] == fingerprint(NEW_KEY)


async def test_changed_prompt_gets_old_key_type_when_it_takes_it(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    store.add(HOP.host, 22, OLD_ECDSA)
    prompter = KwPrompter(hostkey_changed=(True, "web-prod"))
    await verify(prompter, store, events)
    assert prompter.extras == [{"old_key_type": "ecdsa-sha2-nistp256"}]


async def test_portless_known_hosts_line_does_not_cover_another_port(
    tmp_path: Path, store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    # OpenSSH semantics; asyncssh's own match() would fall back to the port-less line.
    path = tmp_path / "known_hosts"
    path.write_text(f"web-prod.example.test {NEW_KEY}\n[web-prod.example.test]:2222 {OLD_KEY}\n")
    known_hosts = FileKnownHosts(path)
    on_2200 = hop(name="web-prod", host="web-prod.example.test", port=2200)
    prompter = FakePrompter(hostkey_new=True)
    await verify_host_key(on_2200, NEW_KEY, prompter, store, known_hosts, events)
    assert prompter.kinds() == ["hostkey_new"]
    on_22 = hop(name="web-prod", host="web-prod.example.test", port=22)
    quiet = FakePrompter()
    await verify_host_key(on_22, NEW_KEY, quiet, store, known_hosts, events)
    assert quiet.kinds() == []


# --- review findings: a replaced known_hosts key; one address behind two jump hosts -------------


def test_a_distrusted_known_hosts_key_counts_as_changed() -> None:
    gone = replace(record(OLD_KEY, rid=1), distrusted=True)
    stored = [gone, record(NEW_KEY, rid=2)]
    decision = decide(OLD_KEY, KnownHostsMatch(trusted=(OLD_KEY,)), stored)
    assert decision.verdict is Verdict.CHANGED
    assert decision.old_fingerprint == fingerprint(NEW_KEY)
    assert decide(NEW_KEY, KnownHostsMatch(trusted=(OLD_KEY,)), stored).verdict is Verdict.TRUSTED
    assert trusted_algorithms(KnownHostsMatch(trusted=(OLD_ECDSA,)), []) != []
    distrusted_ecdsa = replace(record(OLD_ECDSA), distrusted=True)
    assert trusted_algorithms(KnownHostsMatch(trusted=(OLD_ECDSA,)), [distrusted_ecdsa]) == []


def test_changed_against_known_hosts_lists_what_a_replace_supersedes() -> None:
    decision = decide(NEW_KEY, KnownHostsMatch(trusted=(OLD_KEY, OLD_ECDSA)), [])
    assert decision.verdict is Verdict.CHANGED
    assert set(decision.superseded) == {OLD_KEY, OLD_ECDSA}


async def test_a_replaced_known_hosts_key_is_not_trusted_again(
    tmp_path: Path, store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    # The owner's read-only known_hosts lists key A; the host now presents B and the owner
    # replaces the key by typing the name. Before the fix, A stayed trusted with no prompt, so
    # an attacker holding A's private key was accepted silently afterwards.
    path = tmp_path / "known_hosts"
    path.write_text(f"web-prod.example.test {OLD_KEY}\n")
    known_hosts = FileKnownHosts(path)
    replacing = FakePrompter(hostkey_changed=(True, "web-prod"))
    await verify_host_key(HOP, NEW_KEY, replacing, store, known_hosts, events)
    assert replacing.kinds() == ["hostkey_changed"]

    quiet = FakePrompter()
    await verify_host_key(HOP, NEW_KEY, quiet, store, known_hosts, events)
    assert quiet.kinds() == []

    declining = FakePrompter(hostkey_changed=(False, ""))
    with pytest.raises(HostKeyChanged):
        await verify_host_key(HOP, OLD_KEY, declining, store, known_hosts, events)
    assert declining.kinds() == ["hostkey_changed"]
    assert declining.calls[0].detail["old_fingerprint"] == fingerprint(NEW_KEY)
    assert path.read_text() == f"web-prod.example.test {OLD_KEY}\n"  # never edited


async def test_one_address_behind_two_jump_hosts_is_trusted_per_route(
    store: SqliteHostKeyStore, events: SqliteEventLog
) -> None:
    # Before the fix, trust was keyed by host:port only: each open of one site showed a
    # "host key changed" warning and its replacement deleted the other site's key.
    site1 = hop(name="db-site1", host="10.0.0.5", via="11111111-1111-4111-8111-111111111111")
    site2 = hop(name="db-site2", host="10.0.0.5", via="22222222-2222-4222-8222-222222222222")
    no_known = StubKnownHosts(KnownHostsMatch())
    for ref, key in ((site1, OLD_KEY), (site2, NEW_KEY)):
        first = FakePrompter(hostkey_new=True)
        await verify_host_key(ref, key, first, store, no_known, events)
        assert first.kinds() == ["hostkey_new"]
    for ref, key in ((site1, OLD_KEY), (site2, NEW_KEY), (site1, OLD_KEY)):
        again = FakePrompter()
        await verify_host_key(ref, key, again, store, no_known, events)
        assert again.kinds() == []
    # A different key on one route is still a changed key there.
    with pytest.raises(HostKeyChanged):
        await verify_host_key(
            site1, NEW_KEY, FakePrompter(hostkey_changed=(False, "")), store, no_known, events
        )
