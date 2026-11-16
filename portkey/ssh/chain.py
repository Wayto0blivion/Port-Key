"""Jump-chain planning and connection (package 4). Implements ``interfaces.SshConnector``.

``plan`` resolves a saved connection's chain (``ConnectionsRepo.resolve_chain``, outermost
first) into ``HopTarget``s with their own users, keys and auth methods. ``connect`` opens hop 0,
then each next hop with ``tunnel=<previous connection>``, sending ``status`` (connecting,
hostkey, auth per hop, then ready for the target). A jump hop's network or sign-in failure is
wrapped in ``JumpFailed``; host-key outcomes (``hostkey_changed``, ``hostkey_rejected``,
``hostkey_revoked``) keep their own code and close code, with ``hop`` naming the jump. On any
failure or cancellation every hop opened so far is closed, innermost first, before the error
propagates, so nothing is left connecting. Closing the chain closes every hop, target first.
One SSH connection per terminal session; no pooling. DNS failures, refused connections,
timeouts and unreachable networks map to ``DnsError``, ``ConnectionRefused``,
``ConnectTimeout`` and ``NetworkUnreachable``.

``plan`` and ``plan_new`` read SQLite and scan the key folders: they are blocking, so async
callers run them with ``asyncio.to_thread``. ``connect`` does its own blocking work in threads.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

import asyncssh

from portkey.config import Limits
from portkey.errors import Invalid, NotFound
from portkey.interfaces import (
    ConnectionsRepo,
    EventLog,
    HostKeyStore,
    KeyProvider,
    KeyVault,
    KnownHostsReader,
    Prompter,
    SettingsRepo,
)
from portkey.models import ConnectionRecord, HopRef, HopRole, HopTarget
from portkey.ssh._hop import HopDeps, connect_hop
from portkey.ssh.errors import (
    JumpFailed,
    KeyUnreadable,
    ProtocolError,
    SshError,
    UnknownConnection,
    WsErrorCode,
)

log = logging.getLogger("portkey.ssh")

CLOSE_TIMEOUT = 5.0
#: Codes that stay as they are when a jump hop fails (security stops and user decisions).
_UNWRAPPED = {
    WsErrorCode.HOSTKEY_CHANGED,
    WsErrorCode.HOSTKEY_REJECTED,
    WsErrorCode.HOSTKEY_REVOKED,
}

HopConnect = Callable[
    [HopTarget, asyncssh.SSHClientConnection | None, Prompter, HopDeps],
    Awaitable[asyncssh.SSHClientConnection],
]


def too_deep_message(max_hops: int) -> str:
    return f"Jump chains can be at most {max_hops} hosts long. Change a Jump via setting."


class OpenChain:
    """Implements ``interfaces.SshChain``."""

    def __init__(self, connections: list[asyncssh.SSHClientConnection], hops: list[HopRef]):
        self._connections = connections
        self._hops = hops
        self._closed = False

    @property
    def target(self) -> asyncssh.SSHClientConnection:
        return self._connections[-1]

    @property
    def hops(self) -> list[HopRef]:
        return list(self._hops)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await close_all(self._connections)


async def close_all(connections: list[asyncssh.SSHClientConnection]) -> None:
    """Close ``connections`` innermost (last) first; give each a few seconds, then abort."""
    for conn in reversed(connections):
        conn.close()
        try:
            await asyncio.wait_for(conn.wait_closed(), CLOSE_TIMEOUT)
        except (TimeoutError, OSError):
            conn.abort()
        except asyncio.CancelledError:
            for rest in connections:
                rest.abort()
            raise


def abort_all(connections: list[asyncssh.SSHClientConnection]) -> None:
    """Drop every connection at once, innermost first (no waiting: safe while cancelled)."""
    for conn in reversed(connections):
        with contextlib.suppress(Exception):
            conn.abort()


class ChainConnector:
    def __init__(
        self,
        connections: ConnectionsRepo,
        keys: KeyProvider,
        host_keys: HostKeyStore,
        known_hosts: KnownHostsReader,
        events: EventLog,
        settings: SettingsRepo,
        limits: Limits,
        *,
        vault: KeyVault | None = None,
    ) -> None:
        self.connections = connections
        self.keys = keys
        self.host_keys = host_keys
        self.known_hosts = known_hosts
        self.events = events
        self.settings = settings
        self.limits = limits
        self.vault = vault
        self.connect_hop: HopConnect = connect_hop  # replaceable in tests

    # --- planning ------------------------------------------------------------------------------

    def plan(self, conn_id: str, force_password: bool = False) -> list[HopTarget]:
        records = self._chain(conn_id)
        count = len(records)
        return [
            self._target(rec, i, count, force_password=force_password and i == count - 1)
            for i, rec in enumerate(records)
        ]

    def plan_new(self, jump_id: str | None, target: HopTarget) -> list[HopTarget]:
        jumps = self._chain(jump_id) if jump_id is not None else []
        count = len(jumps) + 1
        self._check_depth(count)
        hops = [self._target(rec, i, count) for i, rec in enumerate(jumps)]
        ref = replace(target.ref, index=count - 1, count=count, role="target")
        key_fp = target.key_fp
        if target.auth_method == "key" and key_fp is None and target.allow_key:
            try:
                key_fp = self.keys.resolve(None).fp
            except NotFound:
                raise KeyUnreadable(
                    "Port-Key found no SSH key to use. Generate one in Keys, or add a key file "
                    "to your .ssh folder.",
                    hop=ref,
                ) from None
        return [*hops, replace(target, ref=ref, key_fp=key_fp)]

    def _chain(self, conn_id: str) -> list[ConnectionRecord]:
        try:
            records = self.connections.resolve_chain(conn_id)
        except NotFound:
            raise UnknownConnection() from None
        except Invalid as exc:
            raise ProtocolError(exc.message) from None
        if not records:
            raise UnknownConnection()
        self._check_depth(len(records))
        return records

    def _check_depth(self, count: int) -> None:
        if count - 1 > self.limits.max_jump_hops:
            raise ProtocolError(too_deep_message(self.limits.max_jump_hops))

    def _target(
        self, rec: ConnectionRecord, index: int, count: int, *, force_password: bool = False
    ) -> HopTarget:
        role: HopRole = "target" if index == count - 1 else "jump"
        ref = HopRef(index, count, rec.id, rec.name, rec.host, rec.port, role)
        if rec.auth_method != "key":
            return HopTarget(ref, rec.username, "password", None, allow_key=False)
        try:
            key = self.keys.resolve(rec.key_fp)
        except NotFound:
            if force_password:
                return HopTarget(ref, rec.username, "key", rec.key_fp, allow_key=False)
            raise KeyUnreadable(
                f"Port-Key can't find the key {rec.name} signs in with. Choose another key in "
                "Edit connection, or add the key file to your .ssh folder.",
                hop=ref,
            ) from None
        return HopTarget(ref, rec.username, "key", key.fp, allow_key=not force_password)

    # --- connecting ----------------------------------------------------------------------------

    async def connect(self, hops: list[HopTarget], prompter: Prompter) -> OpenChain:
        if not hops:
            raise ProtocolError("There is nothing to connect to.")
        self._check_depth(len(hops))
        settings = await asyncio.to_thread(self.settings.get)
        deps = HopDeps(
            keys=self.keys,
            host_keys=self.host_keys,
            known_hosts=self.known_hosts,
            events=self.events,
            limits=self.limits,
            vault=self.vault,
            passphrase_ttl=float(settings.passphrase_ttl),
        )
        opened: list[asyncssh.SSHClientConnection] = []
        try:
            for target in hops:
                tunnel = opened[-1] if opened else None
                try:
                    conn = await self.connect_hop(target, tunnel, prompter, deps)
                except SshError as exc:
                    raise self._hop_error(exc, target.ref) from None
                opened.append(conn)
                log.info(
                    "hop %d/%d connected (%s:%d)",
                    target.ref.index + 1,
                    target.ref.count,
                    target.ref.host,
                    target.ref.port,
                )
            await prompter.status("ready", hops[-1].ref)
        except BaseException:
            abort_all(opened)
            raise
        return OpenChain(opened, [h.ref for h in hops])

    @staticmethod
    def _hop_error(exc: SshError, ref: HopRef) -> SshError:
        if exc.hop is None:
            exc.hop = ref
        if ref.role != "jump" or exc.code in _UNWRAPPED:
            log.info("hop %d failed: %s", ref.index, exc.code)
            return exc
        log.info("jump hop %d failed: %s", ref.index, exc.code)
        return JumpFailed(exc, hop=exc.hop)
