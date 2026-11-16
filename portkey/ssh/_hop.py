"""Connecting one hop: fetch the host key, decide, then sign in pinned to the accepted key.

1. ``status connecting``; fetch the presented key with ``asyncssh.get_server_host_key``
   (through the previous hop when there is one), restricted to the algorithms of keys already
   trusted for host:port when there are any, so a multi-key server shows the trusted one.
2. ``hostkey.decide``, then ``status hostkey``, which carries ``source`` (``known_hosts`` or
   ``store``) when the key is trusted already (the wizard's ``known``/``trusted`` code). A key
   that is not trusted outright may still come with a host certificate from an
   ``@cert-authority`` in known_hosts: that is tried first, as a connection that trusts only
   those CAs. Otherwise ``hostkey.act_on`` prompts and stores.
3. Open the authenticated connection with ``known_hosts`` holding exactly the accepted key, so
   a different key at this point fails key exchange before any credential is sent.

Key exchange must finish within ``limits.ssh_connect_timeout`` (asyncssh's own
``connect_timeout`` would also count the minutes a person spends on a prompt); sign-in as a whole
is bounded by ``client.login_timeout``. Cancelling the caller leaves nothing connecting.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, cast

import asyncssh

from portkey.config import Limits
from portkey.interfaces import (
    EventLog,
    HostKeyStore,
    KeyProvider,
    KeyVault,
    KnownHostsReader,
    Prompter,
)
from portkey.models import HopRef, HopTarget, KnownHostsMatch
from portkey.ssh import hostkey
from portkey.ssh._errmap import map_connect_error, timeout_error
from portkey.ssh.client import PortKeySSHClient, login_timeout, ssh_options
from portkey.ssh.errors import ProtocolError, SshError

Conn = asyncssh.SSHClientConnection


@dataclass(frozen=True, slots=True)
class HopDeps:
    keys: KeyProvider
    host_keys: HostKeyStore
    known_hosts: KnownHostsReader
    events: EventLog
    limits: Limits
    vault: KeyVault | None
    passphrase_ttl: float

    @property
    def login_timeout(self) -> float:
        return login_timeout(
            self.limits.ssh_connect_timeout,
            self.limits.prompt_timeout,
            self.limits.max_auth_attempts,
        )


async def fetch_host_key(
    ref: HopRef, tunnel: Conn | None, algs: list[str], seconds: float
) -> asyncssh.SSHKey:
    """The host key ``ref`` presents, within ``seconds``. With ``algs``, only those host key
    algorithms are offered; if the server has none of them, the key it prefers is fetched
    instead."""

    async def fetch(server_algs: list[str] | tuple[()]) -> asyncssh.SSHKey | None:
        return await asyncio.wait_for(
            asyncssh.get_server_host_key(
                ref.host,
                ref.port,
                tunnel=tunnel if tunnel is not None else (),
                config=None,
                server_host_key_algs=server_algs,
            ),
            timeout=seconds,
        )

    key: asyncssh.SSHKey | None = None
    if algs:
        with contextlib.suppress(asyncssh.KeyExchangeFailed):
            key = await fetch(algs)
    if key is None:
        key = await fetch(())
    if key is None:
        raise ProtocolError(f"{ref.host} did not present a host key.", hop=ref)
    return key


async def open_connection(
    target: HopTarget,
    tunnel: Conn | None,
    client: PortKeySSHClient,
    known_hosts: Any,
    deps: HopDeps,
) -> Conn:
    """``asyncssh.connect`` with ``client``; key exchange must finish within the connect
    timeout. The connection attempt never outlives this call."""
    limits = deps.limits
    options = ssh_options(
        target,
        limits.ssh_connect_timeout,
        limits.ssh_keepalive_interval,
        limits.ssh_keepalive_count_max,
    )
    options["login_timeout"] = deps.login_timeout
    task = asyncio.ensure_future(
        asyncssh.connect(
            target.ref.host,
            tunnel=tunnel if tunnel is not None else (),
            client_factory=lambda: client,
            known_hosts=known_hosts,
            **cast(dict[str, Any], options),
        )
    )
    try:
        kex = asyncio.ensure_future(client.kex_done.wait())
        try:
            done, _ = await asyncio.wait(
                {task, kex},
                timeout=limits.ssh_connect_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            kex.cancel()
        if not done:
            raise timeout_error(target.ref, limits.ssh_connect_timeout)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _failure(
    exc: BaseException, client: PortKeySSHClient, ref: HopRef, deps: HopDeps
) -> BaseException:
    """The exception to raise for a failed authenticated connection."""
    if client.failure is not None:
        return client.failure
    if isinstance(exc, asyncssh.PermissionDenied):
        return client.outcome_error()
    mapped = map_connect_error(exc, ref, deps.limits.ssh_connect_timeout, deps.login_timeout)
    return mapped if mapped is not None else exc


_HOSTKEY_REFUSED = (asyncssh.HostKeyNotVerifiable, asyncssh.KeyExchangeFailed)


async def authenticate(
    target: HopTarget,
    tunnel: Conn | None,
    prompter: Prompter,
    known_hosts: Any,
    deps: HopDeps,
    *,
    cert_probe: bool = False,
) -> Conn:
    """Open and sign in. Raises ``asyncssh.HostKeyNotVerifiable`` when the host key is not
    the pinned one (nothing was sent), otherwise ``SshError``/prompt errors. With
    ``cert_probe``, a server without an acceptable host certificate raises
    ``asyncssh.KeyExchangeFailed`` as it is, too."""
    client = PortKeySSHClient(
        target,
        prompter,
        deps.keys,
        deps.host_keys,
        deps.known_hosts,
        deps.events,
        deps.passphrase_ttl,
        vault=deps.vault,
        max_attempts=deps.limits.max_auth_attempts,
    )
    try:
        return await open_connection(target, tunnel, client, known_hosts, deps)
    except _HOSTKEY_REFUSED as exc:
        if client.failure is not None:
            raise client.failure from None
        if isinstance(exc, asyncssh.HostKeyNotVerifiable) or cert_probe:
            raise
        raise _failure(exc, client, target.ref, deps) from None
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        raise _failure(exc, client, target.ref, deps) from None
    except SshError as exc:
        raise _failure(exc, client, target.ref, deps) from None


def _import_keys(texts: tuple[str, ...]) -> list[asyncssh.SSHKey]:
    out = []
    for text in texts:
        with contextlib.suppress(asyncssh.KeyImportError, ValueError):
            out.append(asyncssh.import_public_key(text))
    return out


def _hostkey_source(decision: hostkey.HostKeyDecision) -> dict[str, str]:
    """``source`` detail for ``status hostkey``: where an already-trusted key came from
    (``known_hosts`` or the app ``store``); nothing when the key still needs a decision."""
    if decision.verdict is not hostkey.Verdict.TRUSTED:
        return {}
    return {"source": "store" if decision.trusted_record is not None else "known_hosts"}


async def _try_certificate(
    target: HopTarget,
    tunnel: Conn | None,
    prompter: Prompter,
    known: KnownHostsMatch,
    deps: HopDeps,
) -> Conn | None:
    """Sign in trusting only known_hosts' CAs for this host, or None if the host has no
    certificate those CAs signed (nothing was sent then)."""
    cas = _import_keys(known.cert_authorities)
    if not cas:
        return None
    pin: tuple[list[asyncssh.SSHKey], ...] = ([], cas, _import_keys(known.revoked))
    try:
        return await authenticate(target, tunnel, prompter, pin, deps, cert_probe=True)
    except _HOSTKEY_REFUSED:
        return None


async def connect_hop(
    target: HopTarget, tunnel: Conn | None, prompter: Prompter, deps: HopDeps
) -> Conn:
    """Connect and sign in to one hop (see the module docstring)."""
    ref = target.ref
    timeout = deps.limits.ssh_connect_timeout
    await prompter.status("connecting", ref)
    known, stored = await hostkey.lookup(ref, deps.host_keys, deps.known_hosts)
    try:
        presented = await fetch_host_key(
            ref, tunnel, hostkey.trusted_algorithms(known, stored), timeout
        )
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        mapped = map_connect_error(exc, ref, timeout)
        if mapped is None:
            raise
        raise mapped from None
    presented_text = hostkey.key_text(presented)
    decision = hostkey.decide(presented_text, known, stored)
    await prompter.status("hostkey", ref, **_hostkey_source(decision))
    if decision.verdict in (hostkey.Verdict.NEW, hostkey.Verdict.CHANGED):
        conn = await _try_certificate(target, tunnel, prompter, known, deps)
        if conn is not None:
            return conn
    await hostkey.act_on(ref, presented_text, decision, prompter, deps.host_keys, deps.events)
    try:
        return await authenticate(target, tunnel, prompter, ([presented], [], []), deps)
    except asyncssh.HostKeyNotVerifiable:
        raise ProtocolError(
            f"{ref.host} presented a different host key than the one just checked. Port-Key "
            "sent nothing and closed the connection. Try again.",
            hop=ref,
        ) from None
