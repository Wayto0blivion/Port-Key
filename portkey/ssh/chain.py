"""Jump-chain planning and connection (package 4). Implements ``interfaces.SshConnector``.

``plan`` resolves a saved connection's chain (``ConnectionsRepo.resolve_chain``, outermost
first) into ``HopTarget``s with their own users, keys and auth methods. ``connect`` opens hop 0,
then each next hop with ``tunnel=<previous connection>``, sending ``status`` (connecting,
hostkey, auth, ready) per hop. A failing jump hop is wrapped in ``JumpFailed``. Closing the chain
closes every hop, target first. One SSH connection per terminal session; no pooling. DNS
failures, refused connections, timeouts and unreachable networks map to ``DnsError``,
``ConnectionRefused``, ``ConnectTimeout`` and ``NetworkUnreachable``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from portkey.config import Limits
from portkey.interfaces import (
    ConnectionsRepo,
    EventLog,
    HostKeyStore,
    KeyProvider,
    KnownHostsReader,
    Prompter,
    SettingsRepo,
)
from portkey.models import HopRef, HopTarget

if TYPE_CHECKING:
    import asyncssh


class OpenChain:
    """Implements ``interfaces.SshChain``."""

    def __init__(self, connections: list[asyncssh.SSHClientConnection], hops: list[HopRef]):
        self._connections = connections
        self._hops = hops

    @property
    def target(self) -> asyncssh.SSHClientConnection:
        return self._connections[-1]

    @property
    def hops(self) -> list[HopRef]:
        return list(self._hops)

    async def close(self) -> None:
        raise NotImplementedError("package 4")


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
    ) -> None:
        self.connections = connections
        self.keys = keys
        self.host_keys = host_keys
        self.known_hosts = known_hosts
        self.events = events
        self.settings = settings
        self.limits = limits

    def plan(self, conn_id: str, force_password: bool = False) -> list[HopTarget]:
        raise NotImplementedError("package 4")

    def plan_new(self, jump_id: str | None, target: HopTarget) -> list[HopTarget]:
        raise NotImplementedError("package 4")

    async def connect(self, hops: list[HopTarget], prompter: Prompter) -> OpenChain:
        raise NotImplementedError("package 4")
