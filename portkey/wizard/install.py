"""The key install flow behind /ws/install (package 6).

Steps (docs/architecture.md section 8, docs/ws-protocol.md): validate -> connect (jump hops
first) -> hostkey -> try_key -> password_login -> install (``remote_script``) -> verify (a new
key-only connection) -> discard_password -> save (create, or update ``connection_id``; record
``key_installed``). Recoverable failures send ``needs_action`` and wait for an ``action``.

The first connection offers the key first and, if the server refuses it, the password (and
keyboard-interactive, so 2FA is relayed) on the same connection: if the key works the run saves
and finishes with ``already_installed``; otherwise that connection is the signed-in one the
script runs on. With ``assume_installed`` it offers the key only. Verify, "Check key login" and
"Install it with a password" open new connections through the same jump chain.

The password exists only in the prompt reply and inside the SSH call: this module never sees
it. Dropping it means closing the password-authenticated connection, which happens before the
save (``discard_password``) and in any case when the run ends.

Blocking repository calls run through ``asyncio.to_thread``. The run state machine is in
``_flow``; this module holds the parts that touch storage.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, Protocol

from portkey.api.schemas.ws import NeedsActionMsg, StartMsg, StepMsg, WizardAction
from portkey.errors import ErrorCode, Invalid, NotFound, PortKeyError
from portkey.interfaces import (
    ConnectionsRepo,
    EventLog,
    GroupsRepo,
    HostKeyStore,
    KeyProvider,
    KnownHostsReader,
    Prompter,
    SshConnector,
)
from portkey.models import ConnectionDraft, ConnectionRecord, HopRef, KeyRecord
from portkey.ssh.errors import KeyUnreadable, UnknownConnection
from portkey.wizard._flow import Run
from portkey.wizard.errors import InputRejected
from portkey.wizard.remote_script import KeyLine, parse_key_line

log = logging.getLogger("portkey.wizard")

Outcome = Literal["installed", "already_installed", "saved_password_only"]


class WizardIO(Protocol):
    """How the flow talks to the browser beyond prompts; implemented in ``api.ws_install``."""

    async def step(self, msg: StepMsg) -> None: ...

    async def needs_action(self, msg: NeedsActionMsg) -> WizardAction:
        """Send the message and wait for the user's ``action``."""
        ...


class InstallWizard:
    """``groups``, ``known_hosts`` and ``host_keys`` are optional (``services.build_services``
    does not pass them yet). Without ``groups`` an unknown folder is found only at save time;
    ``known_hosts``/``host_keys`` default to the connector's own (``ChainConnector`` has both)
    and give the ``hostkey`` step its ``known``/``trusted`` code."""

    def __init__(
        self,
        connector: SshConnector,
        connections: ConnectionsRepo,
        keys: KeyProvider,
        events: EventLog,
        *,
        groups: GroupsRepo | None = None,
        known_hosts: KnownHostsReader | None = None,
        host_keys: HostKeyStore | None = None,
        script_timeout: float = 60.0,
    ) -> None:
        self.connector = connector
        self.connections = connections
        self.keys = keys
        self.events = events
        self.groups = groups
        self.known_hosts = known_hosts or getattr(connector, "known_hosts", None)
        self.host_keys = host_keys or getattr(connector, "host_keys", None)
        self.script_timeout = script_timeout

    async def run(
        self, start: StartMsg, prompter: Prompter, io: WizardIO
    ) -> tuple[ConnectionRecord, str]:
        """Run the whole flow. Returns (saved connection, outcome) where outcome is
        "installed", "already_installed" or "saved_password_only". Raises ``SshError``
        subclasses, ``PromptCancelled`` (also for the ``cancel`` action), ``PromptTimeout``,
        ``UnknownConnection`` (unknown ``jump_id``/``connection_id``), ``InputRejected`` (the
        connection cannot be saved with those settings) and ``BadMessage``."""
        return await Run(self, start, prompter, io).execute()

    # --- blocking helpers (call through asyncio.to_thread) ------------------------------------

    def check_input(self, start: StartMsg) -> tuple[KeyRecord, KeyLine]:
        """Everything that can be checked before connecting: the connection and jump ids, the
        name, the folder, and that the key and its public half can be read."""
        conns = self.connections
        if start.connection_id is not None and conns.find(start.connection_id) is None:
            raise UnknownConnection()
        if start.jump_id is not None:
            try:
                conns.validate_jump(start.connection_id, start.jump_id)
            except Invalid as exc:
                raise _rejected(exc) from None
        folded = start.name.strip().casefold()
        for other in conns.list_all():
            if other.id != start.connection_id and other.name.casefold() == folded:
                raise InputRejected(
                    f"A connection named {start.name.strip()} already exists.",
                    code=ErrorCode.NAME_TAKEN,
                )
        if start.group_id is not None and self.groups is not None:
            try:
                self.groups.get(start.group_id)
            except NotFound:
                raise InputRejected(
                    "That folder no longer exists. Choose another one.",
                    code=ErrorCode.GROUP_UNKNOWN,
                ) from None
        try:
            key = self.keys.resolve(start.key_fp)
            if key.hidden:  # as the HTTP API: a hidden key counts as unknown
                raise InputRejected(
                    f"{key.name} is hidden from Port-Key. Choose another key, or unhide it in "
                    "Keys first.",
                    code=ErrorCode.KEY_UNKNOWN,
                )
        except NotFound:
            raise KeyUnreadable(
                "Port-Key can't find that key any more. Choose another key, or add the key file "
                "to your .ssh folder."
                if start.key_fp
                else "Port-Key found no SSH key to use. Generate one in Keys, or add a key file "
                "to your .ssh folder."
            ) from None
        try:
            line = parse_key_line(key.public_key)
        except ValueError:
            raise KeyUnreadable(
                f"Port-Key can't read the public half of {key.name}, so it can't install it. "
                "Check that the matching .pub file is next to it."
            ) from None
        return key, line

    def save(
        self, start: StartMsg, outcome: Outcome, key: KeyRecord, target: HopRef
    ) -> ConnectionRecord:
        """Create the connection (or update ``connection_id``) and record the event. A key
        connection is saved with the fingerprint of the key that was installed or verified,
        never ``null`` (the default key): the host has that key, and a later change of the
        default key must not switch the connection to one the host does not have."""
        auth = "password" if outcome == "saved_password_only" else "key"
        fields = {
            "name": start.name,
            "host": start.host,
            "port": start.port,
            "username": start.username,
            "auth_method": auth,
            "key_fp": key.fp if auth == "key" else None,
            "jump_id": start.jump_id,
            "group_id": start.group_id,
            "tags": tuple(start.tags),
            "color": start.color,
            "pinned": start.pinned,
            "notes": start.notes,
        }
        try:
            if start.connection_id is not None:
                record = self.connections.update(start.connection_id, fields)
            else:
                record = self.connections.create(ConnectionDraft(**fields))  # type: ignore[arg-type]
        except NotFound:
            raise UnknownConnection() from None
        except PortKeyError as exc:
            raise _rejected(exc) from None
        if auth == "key":
            self.events.record(
                "key_installed",
                record.id,
                host=target.host.lower(),
                port=target.port,
                user=start.username,
                fp=key.fp,
                outcome=outcome,
            )
        log.info("saved connection %s (%s)", record.id, outcome)
        return record

    async def hostkey_code(self, hop: HopRef) -> str | None:
        """``known`` or ``trusted`` for a target whose key was not prompted for: where the
        accepted key most likely came from (only used when the SSH core does not say, as for a
        host certificate). App records on the hop's route win when both sources have keys."""
        known_hosts, host_keys = self.known_hosts, self.host_keys

        def read() -> str | None:
            stored = (
                host_keys.for_host(hop.host, hop.port, hop.via) if host_keys is not None else []
            )
            if any(not r.distrusted for r in stored):
                return "trusted"
            if known_hosts is not None and known_hosts.lookup(hop.host, hop.port).trusted:
                return "known"
            return None

        return await asyncio.to_thread(read)


def _rejected(exc: PortKeyError) -> Exception:
    if exc.code in (ErrorCode.JUMP_UNKNOWN, ErrorCode.NOT_FOUND):
        return UnknownConnection("The jump host connection no longer exists.")
    return InputRejected.from_error(exc)
