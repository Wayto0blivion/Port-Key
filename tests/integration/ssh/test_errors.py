"""Network failures map to the stable codes with the UX copy."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable

import pytest

from portkey.services import Services
from portkey.ssh.errors import (
    CloseCode,
    ConnectionRefused,
    ConnectTimeout,
    DnsError,
    UnknownConnection,
)
from tests.fixtures.prompter import FakePrompter
from tests.integration.ssh.helpers import add_conn, open_chain


@pytest.fixture
async def silent_port() -> AsyncIterator[int]:
    """A local TCP port that accepts connections and never says anything (no SSH banner)."""
    held: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    for writer in held:
        writer.close()
    server.close()
    await server.wait_closed()


async def test_a_name_that_does_not_resolve(svc: Services) -> None:
    conn = add_conn(svc, "ghost", "no-such-host.invalid", "keyonly")
    with pytest.raises(DnsError) as failed:
        await open_chain(svc, conn.id, FakePrompter())
    assert failed.value.close_code == CloseCode.SSH_FAILURE
    assert failed.value.message.startswith("Couldn't find a host named no-such-host.invalid.")


async def test_nothing_listening_is_refused(svc: Services) -> None:
    conn = add_conn(svc, "closed-port", "sshd", "keyonly", port=2200)
    prompter = FakePrompter()
    with pytest.raises(ConnectionRefused) as failed:
        await open_chain(svc, conn.id, prompter)
    assert failed.value.code == "refused"
    assert failed.value.hop is not None
    assert failed.value.hop.port == 2200
    assert prompter.phases() == [(0, "connecting")]


async def test_no_ssh_banner_times_out(svc_with: Callable[..., Services], silent_port: int) -> None:
    svc = svc_with(ssh_connect_timeout=1.0)
    conn = add_conn(svc, "silent", "127.0.0.1", "keyonly", port=silent_port)
    started = time.monotonic()
    with pytest.raises(ConnectTimeout) as failed:
        await open_chain(svc, conn.id, FakePrompter())
    assert time.monotonic() - started < 5
    assert "timed out after 1 second" in failed.value.message


async def test_an_unknown_connection(svc: Services) -> None:
    with pytest.raises(UnknownConnection) as failed:
        svc.connector.plan("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a")
    assert failed.value.close_code == CloseCode.UNKNOWN_CONNECTION
