"""Turning network and asyncssh failures into the stable codes of ``ssh.errors``.

The messages are the copy in docs/ux-spec.md ("Failures"). They name hosts, ports and users
only; asyncssh's own text is never copied into a message, because a disconnect reason is chosen
by the server and may carry anything.
"""

from __future__ import annotations

import errno
import socket

import asyncssh

from portkey.models import HopRef
from portkey.ssh.errors import (
    ConnectionRefused,
    ConnectTimeout,
    DnsError,
    NetworkUnreachable,
    ProtocolError,
    SshError,
)

_UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EHOSTDOWN, errno.ENETDOWN}


def _seconds(value: float) -> str:
    n = round(value)
    return "1 second" if n == 1 else f"{n} seconds"


def dns_error(hop: HopRef) -> DnsError:
    return DnsError(
        f"Couldn't find a host named {hop.host}. Check the spelling or use its IP address. "
        "Port-Key looks names up from inside its container, so names that exist only in the "
        "Windows hosts file may not resolve.",
        hop=hop,
    )


def timeout_error(hop: HopRef, seconds: float) -> ConnectTimeout:
    return ConnectTimeout(
        f"Couldn't reach {hop.host} on port {hop.port}. The connection timed out after "
        f"{_seconds(seconds)}. Check that the host is on and, for VPN hosts, that the VPN is "
        "connected.",
        hop=hop,
    )


def refused_error(hop: HopRef) -> ConnectionRefused:
    return ConnectionRefused(
        f"{hop.host} refused the connection on port {hop.port}. The host is up but nothing is "
        "listening for SSH on that port. Check the port number or start sshd on the host.",
        hop=hop,
    )


def unreachable_error(hop: HopRef) -> NetworkUnreachable:
    return NetworkUnreachable(
        f"Couldn't reach {hop.host} on port {hop.port}: there is no route to it from Port-Key. "
        "Check the address and, for VPN hosts, that the VPN is connected.",
        hop=hop,
    )


def login_timeout_error(hop: HopRef, seconds: float) -> ConnectTimeout:
    return ConnectTimeout(
        f"Signing in to {hop.name} took longer than {round(seconds / 60)} minutes, so Port-Key "
        "gave up. Try again.",
        hop=hop,
    )


def _from_channel_error(exc: asyncssh.ChannelOpenError, hop: HopRef) -> SshError:
    """A jump host could not open the TCP connection onward (sshd's direct-tcpip reason)."""
    reason = exc.reason.lower()
    if "refused" in reason:
        return refused_error(hop)
    if "timed out" in reason or "timeout" in reason:
        return ConnectTimeout(
            f"Couldn't reach {hop.host} on port {hop.port} from the jump host. The connection "
            "timed out. Check that the host is on.",
            hop=hop,
        )
    if any(s in reason for s in ("not known", "resolve", "getaddrinfo", "no address", "name")):
        return dns_error(hop)
    if "prohibited" in reason or exc.code == asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED:
        return ProtocolError(
            f"The jump host would not open a connection to {hop.host}. It may not allow TCP "
            "forwarding (AllowTcpForwarding).",
            hop=hop,
        )
    return unreachable_error(hop)


def map_connect_error(
    exc: BaseException, hop: HopRef, connect_timeout: float, login_timeout: float = 0.0
) -> SshError | None:
    """The ``SshError`` for a failure while reaching or talking to ``hop``, or None when
    ``exc`` is not a network or SSH failure (the caller re-raises it)."""
    if isinstance(exc, SshError):
        return exc
    if isinstance(exc, socket.gaierror):
        return dns_error(hop)
    if isinstance(exc, ConnectionRefusedError):
        return refused_error(hop)
    if isinstance(exc, TimeoutError):
        return timeout_error(hop, connect_timeout)
    if isinstance(exc, asyncssh.ChannelOpenError):
        return _from_channel_error(exc, hop)
    if isinstance(exc, asyncssh.ConnectionLost):
        if "login timeout" in str(exc).lower():
            return login_timeout_error(hop, login_timeout)
        return ProtocolError(
            f"{hop.host} closed the connection on port {hop.port} before signing in finished.",
            hop=hop,
        )
    if isinstance(exc, asyncssh.KeyExchangeFailed):
        return ProtocolError(
            f"Port-Key and {hop.host} have no SSH key exchange or host key type in common.",
            hop=hop,
        )
    if isinstance(exc, asyncssh.ProtocolNotSupported):
        return ProtocolError(
            f"{hop.host} on port {hop.port} does not speak a supported SSH version.", hop=hop
        )
    if isinstance(exc, asyncssh.DisconnectError):
        return ProtocolError(
            f"{hop.host} ended the SSH connection on port {hop.port}.",
            hop=hop,
        )
    if isinstance(exc, OSError):
        if exc.errno in _UNREACHABLE_ERRNOS:
            return unreachable_error(hop)
        if exc.errno == errno.ECONNREFUSED:
            return refused_error(hop)
        if exc.errno == errno.ETIMEDOUT:
            return timeout_error(hop, connect_timeout)
        return unreachable_error(hop)
    return None
