"""Host-key decisions (package 4).

Before any credential is sent, the key a hop presents (fetched with
``asyncssh.get_server_host_key``) is checked, and the authenticated connection is then pinned to
exactly the accepted key, so there is no window in which a different key is accepted:

=================================================  ===========  ===========================
Situation                                          Verdict      Action
=================================================  ===========  ===========================
key listed ``@revoked`` in known_hosts             REVOKED      raise ``HostKeyRevoked``
key in known_hosts or the app store                TRUSTED      touch app record, continue
no trusted record for host:port at all             NEW          ``hostkey_new`` prompt;
                                                                accept -> ``store.add``,
                                                                event ``hostkey_accepted``;
                                                                decline -> ``HostKeyRejected``
host:port has trusted records, key not among them  CHANGED      ``hostkey_changed`` prompt;
                                                                accept + name matches ->
                                                                ``store.replace``, event
                                                                ``hostkey_replaced``; else
                                                                ``HostKeyChanged`` (4409)
=================================================  ===========  ===========================

A host certificate signed by an ``@cert-authority`` entry counts as TRUSTED; ``ssh.chain``
checks that path before a NEW or CHANGED prompt. Each hop is checked on its own.

known_hosts matching follows OpenSSH, not asyncssh: a host on a port other than 22 is looked up
only as ``[host]:port``, so a port-less ``host`` line does not vouch for ``host:2200``
(asyncssh's own ``match()`` would fall back to it). ``keys.known_hosts`` implements the lookup;
asyncssh is only ever handed explicit key lists.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import asyncssh

from portkey.interfaces import EventLog, HostKeyStore, KnownHostsReader, Prompter
from portkey.models import HopRef, HostKeyRecord, KnownHostsMatch
from portkey.ssh._compat import call_prompter
from portkey.ssh.errors import HostKeyChanged, HostKeyRejected, HostKeyRevoked

log = logging.getLogger("portkey.ssh")


class Verdict(StrEnum):
    TRUSTED = "trusted"
    NEW = "new"
    CHANGED = "changed"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class HostKeyDecision:
    verdict: Verdict
    key_type: str
    fingerprint: str
    trusted_record: HostKeyRecord | None = None  # TRUSTED via the app store
    old_key_type: str | None = None  # CHANGED
    old_fingerprint: str | None = None  # CHANGED
    old_first_seen_at: datetime | None = None  # CHANGED, None if the old key is in known_hosts


def normalize_key(public_key: str) -> str:
    """``"<type> <base64>"`` of an OpenSSH public key line, comment dropped. Raises
    ``ValueError`` when the text is not one."""
    parts = public_key.strip().split()
    if len(parts) < 2:
        raise ValueError("not an OpenSSH public key")
    try:
        base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("not an OpenSSH public key") from None
    return f"{parts[0]} {parts[1]}"


def fingerprint(public_key: str) -> str:
    """``SHA256:<base64 without padding>`` of an OpenSSH public key line."""
    blob = base64.b64decode(normalize_key(public_key).split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return "SHA256:" + digest.rstrip("=")


def key_text(key: asyncssh.SSHKey) -> str:
    """``"<type> <base64>"`` of an asyncssh public key."""
    return normalize_key(bytes(key.export_public_key("openssh")).decode("ascii"))


def _key_type(public_key: str) -> str:
    return public_key.split()[0]


def _safe_normalize(public_key: str) -> str | None:
    try:
        return normalize_key(public_key)
    except ValueError:
        return None


def decide(
    presented_key: str, known: KnownHostsMatch, stored: list[HostKeyRecord]
) -> HostKeyDecision:
    """Pure decision table above. ``presented_key`` is OpenSSH public-key text."""
    key = normalize_key(presented_key)
    key_type, fp = _key_type(key), fingerprint(key)
    revoked = {_safe_normalize(k) for k in known.revoked}
    if key in revoked:
        return HostKeyDecision(Verdict.REVOKED, key_type, fp)
    trusted = [k for k in (_safe_normalize(t) for t in known.trusted) if k and k not in revoked]
    if key in trusted:
        return HostKeyDecision(Verdict.TRUSTED, key_type, fp)
    usable = [r for r in stored if _safe_normalize(r.public_key) not in revoked]
    for record in usable:
        if _safe_normalize(record.public_key) == key:
            return HostKeyDecision(Verdict.TRUSTED, key_type, fp, trusted_record=record)
    if not trusted and not usable:
        return HostKeyDecision(Verdict.NEW, key_type, fp)
    # CHANGED: show the old key most comparable to the new one: the same type first, the app's
    # own record (which knows when it was first seen) before the owner's known_hosts.
    same_stored = [r for r in usable if r.key_type == key_type]
    same_known = [k for k in trusted if _key_type(k) == key_type]
    if same_stored or (usable and not same_known):
        old = (same_stored or usable)[0]
        return HostKeyDecision(
            Verdict.CHANGED,
            key_type,
            fp,
            old_key_type=old.key_type,
            old_fingerprint=old.fp_sha256,
            old_first_seen_at=old.first_seen_at,
        )
    old_key = (same_known or trusted)[0]
    return HostKeyDecision(
        Verdict.CHANGED,
        key_type,
        fp,
        old_key_type=_key_type(old_key),
        old_fingerprint=fingerprint(old_key),
    )


def confirm_name_matches(typed: str, expected: str) -> bool:
    """The typed-name check for a changed key: equal after stripping surrounding whitespace,
    case-insensitive."""
    want = expected.strip().casefold()
    return bool(want) and typed.strip().casefold() == want


def trusted_algorithms(known: KnownHostsMatch, stored: list[HostKeyRecord]) -> list[str]:
    """Host key algorithms of every key already trusted for host:port, in order. Fetching the
    presented key with only these lets a server with several host keys present the one the owner
    already trusts (as OpenSSH does), instead of a false "changed" for another key type."""
    revoked = {_safe_normalize(k) for k in known.revoked}
    algs: list[str] = []
    for text in [*known.trusted, *(r.public_key for r in stored)]:
        if _safe_normalize(text) in revoked:
            continue
        try:
            key = asyncssh.import_public_key(text)
        except (asyncssh.KeyImportError, ValueError):
            continue
        for alg in key.sig_algorithms:
            name = alg.decode("ascii")
            if name not in algs:
                algs.append(name)
    return algs


async def lookup(
    hop: HopRef, store: HostKeyStore, known_hosts: KnownHostsReader
) -> tuple[KnownHostsMatch, list[HostKeyRecord]]:
    """known_hosts entries and app records for the hop's host:port (off the event loop)."""

    def read() -> tuple[KnownHostsMatch, list[HostKeyRecord]]:
        return known_hosts.lookup(hop.host, hop.port), store.for_host(hop.host, hop.port)

    return await asyncio.to_thread(read)


async def act_on(
    hop: HopRef,
    presented_key: str,
    decision: HostKeyDecision,
    prompter: Prompter,
    store: HostKeyStore,
    events: EventLog,
) -> None:
    """Carry out ``decision``: return when the key is trusted (storing it if the user accepted
    it), otherwise raise. Nothing has been sent to the host when this raises."""
    where = f"{hop.name} ({hop.host})" if hop.name != hop.host else hop.host
    if decision.verdict is Verdict.REVOKED:
        log.warning("hop %s: host key %s is revoked", hop.index, decision.fingerprint)
        raise HostKeyRevoked(
            f"{where} presented a host key that your known_hosts marks as revoked. Port-Key "
            "refused it and sent nothing.",
            hop=hop,
        )
    if decision.verdict is Verdict.TRUSTED:
        if decision.trusted_record is not None:
            await asyncio.to_thread(store.touch, decision.trusted_record.id)
        return
    if decision.verdict is Verdict.NEW:
        if not await prompter.hostkey_new(hop, decision.key_type, decision.fingerprint):
            raise HostKeyRejected(
                f"You didn't trust {where}'s host key, so Port-Key closed the connection. "
                "Nothing was sent to the host.",
                hop=hop,
            )
        await asyncio.to_thread(_store_new, hop, presented_key, decision, store, events)
        return
    accept, typed = await call_prompter(
        prompter.hostkey_changed,
        hop,
        decision.key_type,
        decision.old_fingerprint or "",
        decision.fingerprint,
        decision.old_first_seen_at,
        old_key_type=decision.old_key_type,
    )
    if not (accept and confirm_name_matches(typed, hop.name)):
        raise HostKeyChanged(
            f"{where}'s host key has changed. Port-Key sent no password and no key, and the "
            "connection is closed.",
            hop=hop,
        )
    await asyncio.to_thread(_store_replaced, hop, presented_key, decision, store, events)


def _store_new(
    hop: HopRef, key: str, decision: HostKeyDecision, store: HostKeyStore, events: EventLog
) -> None:
    store.add(hop.host, hop.port, key)
    events.record(
        "hostkey_accepted",
        hop.connection_id,
        host=hop.host.lower(),
        port=hop.port,
        fp=decision.fingerprint,
    )
    log.info("hop %s: trusted new host key %s", hop.index, decision.fingerprint)


def _store_replaced(
    hop: HopRef, key: str, decision: HostKeyDecision, store: HostKeyStore, events: EventLog
) -> None:
    store.replace(hop.host, hop.port, key)
    events.record(
        "hostkey_replaced",
        hop.connection_id,
        host=hop.host.lower(),
        port=hop.port,
        old_fp=decision.old_fingerprint,
        new_fp=decision.fingerprint,
    )
    log.info(
        "hop %s: replaced host key %s with %s",
        hop.index,
        decision.old_fingerprint,
        decision.fingerprint,
    )


async def verify_host_key(
    hop: HopRef,
    presented_key: str,
    prompter: Prompter,
    store: HostKeyStore,
    known_hosts: KnownHostsReader,
    events: EventLog,
) -> None:
    """Run the decision and its prompt. Returns only when the key is trusted; otherwise raises
    ``HostKeyRevoked``, ``HostKeyRejected``, ``HostKeyChanged``, ``PromptCancelled`` or
    ``PromptTimeout``."""
    known, stored = await lookup(hop, store, known_hosts)
    decision = decide(presented_key, known, stored)
    await act_on(hop, presented_key, decision, prompter, store, events)
