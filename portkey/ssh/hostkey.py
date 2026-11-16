"""Host-key decisions (package 4).

Before any credential is sent, the presented key (``asyncssh.get_server_host_key`` or the
``validate_host_public_key`` callback) is checked:

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

A key signed by an ``@cert-authority`` entry counts as TRUSTED. Each hop is checked on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from portkey.interfaces import EventLog, HostKeyStore, KnownHostsReader, Prompter
from portkey.models import HopRef, HostKeyRecord, KnownHostsMatch


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


def fingerprint(public_key: str) -> str:
    """``SHA256:<base64 without padding>`` of an OpenSSH public key line."""
    raise NotImplementedError("package 4")


def decide(
    presented_key: str, known: KnownHostsMatch, stored: list[HostKeyRecord]
) -> HostKeyDecision:
    """Pure decision table above. ``presented_key`` is OpenSSH public-key text."""
    raise NotImplementedError("package 4")


def confirm_name_matches(typed: str, expected: str) -> bool:
    """The typed-name check for a changed key: equal after stripping surrounding whitespace,
    case-insensitive."""
    raise NotImplementedError("package 4")


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
    raise NotImplementedError("package 4")
