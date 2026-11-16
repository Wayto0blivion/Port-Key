"""Network and asyncssh failures map to the stable codes with the UX copy."""

from __future__ import annotations

import errno
import socket

import asyncssh
import pytest

from portkey.ssh._errmap import map_connect_error
from portkey.ssh.errors import CloseCode, SshError
from tests.unit.ssh.fakes import hop

HOP = hop(name="imaging-01", host="192.168.10.31", port=2222)


def mapped(exc: BaseException) -> SshError:
    result = map_connect_error(exc, HOP, 10.0, 600.0)
    assert result is not None
    assert result.hop == HOP
    assert result.close_code == CloseCode.SSH_FAILURE
    return result


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (socket.gaierror(-2, "Name or service not known"), "dns"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), "refused"),
        (TimeoutError(), "timeout"),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), "unreachable"),
        (OSError(errno.EHOSTUNREACH, "No route to host"), "unreachable"),
        (OSError(errno.ETIMEDOUT, "timed out"), "timeout"),
        (OSError(errno.ECONNREFUSED, "refused"), "refused"),
        (asyncssh.KeyExchangeFailed("no matching host key type"), "protocol"),
        (asyncssh.ConnectionLost("Connection lost"), "protocol"),
        (asyncssh.ProtocolNotSupported("SSH-1.99 only"), "protocol"),
        (asyncssh.DisconnectError(asyncssh.DISC_BY_APPLICATION, "bye"), "protocol"),
    ],
)
def test_codes(exc: BaseException, code: str) -> None:
    assert mapped(exc).code == code


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("Connection refused", "refused"),
        ("connect failed: Connection timed out", "timeout"),
        ("Name or service not known", "dns"),
        ("getaddrinfo: Temporary failure in name resolution", "dns"),
        ("No route to host", "unreachable"),
    ],
)
def test_jump_host_channel_failures(reason: str, code: str) -> None:
    exc = asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, reason)
    assert mapped(exc).code == code


def test_forwarding_prohibited_on_the_jump_host() -> None:
    exc = asyncssh.ChannelOpenError(asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "denied")
    result = mapped(exc)
    assert result.code == "protocol"
    assert "AllowTcpForwarding" in result.message


def test_ux_copy() -> None:
    assert mapped(TimeoutError()).message == (
        "Couldn't reach 192.168.10.31 on port 2222. The connection timed out after 10 "
        "seconds. Check that the host is on and, for VPN hosts, that the VPN is connected."
    )
    assert mapped(ConnectionRefusedError()).message == (
        "192.168.10.31 refused the connection on port 2222. The host is up but nothing is "
        "listening for SSH on that port. Check the port number or start sshd on the host."
    )
    assert mapped(socket.gaierror()).message.startswith(
        "Couldn't find a host named 192.168.10.31. Check the spelling or use its IP address."
    )


def test_login_timeout() -> None:
    result = mapped(asyncssh.ConnectionLost("Login timeout expired"))
    assert result.code == "timeout"
    assert "10 minutes" in result.message


def test_server_text_never_reaches_the_message() -> None:
    reason = "hunter2-from-the-server"
    for exc in (
        asyncssh.DisconnectError(asyncssh.DISC_BY_APPLICATION, reason),
        asyncssh.ConnectionLost(reason),
        asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, reason),
    ):
        assert reason not in mapped(exc).message


def test_an_existing_ssh_error_passes_through() -> None:
    exc = SshError("already mapped", hop=HOP)
    assert map_connect_error(exc, HOP, 10.0) is exc


def test_unrelated_exceptions_are_not_mapped() -> None:
    assert map_connect_error(ValueError("bug"), HOP, 10.0) is None
    assert map_connect_error(KeyError("bug"), HOP, 10.0) is None
