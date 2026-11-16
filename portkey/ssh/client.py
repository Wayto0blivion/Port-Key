"""The asyncssh client that connects auth callbacks to the ``Prompter`` (package 4).

``PortKeySSHClient`` is passed as ``client_factory`` to ``asyncssh.connect`` for one hop:
- ``validate_host_public_key`` -> ``hostkey.verify_host_key`` (runs before any credential);
- public-key auth uses the hop's key from ``KeyProvider.load_private``; an encrypted key not in
  the vault triggers ``Prompter.passphrase`` (3 tries, then ``KeyLocked``), and ``remember``
  stores it in the vault for ``settings.passphrase_ttl``;
- password auth -> ``Prompter.password`` (retry=True after a failure; 3 tries);
- keyboard-interactive -> ``Prompter.kbdint`` (2FA/OTP relayed verbatim);
- password-change requests -> ``PasswordExpired``.
Methods a ``HopTarget`` disallows are declined. Secrets are dropped right after use.
"""

from __future__ import annotations

import asyncssh

from portkey.interfaces import EventLog, HostKeyStore, KeyProvider, KnownHostsReader, Prompter
from portkey.models import HopTarget


class PortKeySSHClient(asyncssh.SSHClient):
    def __init__(
        self,
        hop: HopTarget,
        prompter: Prompter,
        keys: KeyProvider,
        host_keys: HostKeyStore,
        known_hosts: KnownHostsReader,
        events: EventLog,
        passphrase_ttl: float,
    ) -> None:
        self.hop = hop
        self.prompter = prompter
        self.keys = keys
        self.host_keys = host_keys
        self.known_hosts = known_hosts
        self.events = events
        self.passphrase_ttl = passphrase_ttl
        self.offered_methods: list[str] = []


def ssh_options(
    hop: HopTarget, connect_timeout: float, keepalive: float, count_max: int
) -> dict[str, object]:
    """Keyword arguments for ``asyncssh.connect`` for one hop: username, port,
    ``known_hosts=None`` (checking is done by the client callback), keepalive, timeouts and the
    preferred auth order publickey, keyboard-interactive, password."""
    raise NotImplementedError("package 4")
