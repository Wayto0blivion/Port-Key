"""Helpers for package 4's integration tests (fixtures are in ``conftest.py``).

- ``add_conn(svc, name, host, user, ...)``: save a connection and return its record.
- ``open_chain(svc, conn_id, prompter, force_password=False)``: plan and connect.
- ``BlockingPrompter``: a ``FakePrompter`` whose chosen prompt kind waits forever (for
  cancellation tests); ``asked`` is set once that prompt is up.
- ``established_to(port)``: ESTABLISHED TCP connections from this container to ``port``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import asyncssh

from portkey.models import ConnectionDraft, ConnectionRecord, HopRef
from portkey.services import Services
from portkey.ssh.chain import OpenChain
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter


def key_fp(name: str) -> str:
    """Fingerprint of a fixture client key."""
    return tb.fingerprint(tb.client_public(name))


def add_conn(
    svc: Services,
    name: str,
    host: str,
    user: str,
    *,
    port: int = 22,
    auth_method: str = "key",
    key: str | None = None,
    jump: ConnectionRecord | None = None,
) -> ConnectionRecord:
    return svc.connections.create(
        ConnectionDraft(
            name=name,
            host=host,
            port=port,
            username=user,
            auth_method=auth_method,  # type: ignore[arg-type]
            key_fp=key_fp(key) if key else None,
            jump_id=jump.id if jump else None,
        )
    )


async def open_chain(
    svc: Services, conn_id: str, prompter: Any, *, force_password: bool = False
) -> OpenChain:
    hops = await asyncio.to_thread(svc.connector.plan, conn_id, force_password)
    chain = await svc.connector.connect(hops, prompter)
    assert isinstance(chain, OpenChain)
    return chain


async def whoami(conn: asyncssh.SSHClientConnection) -> str:
    result = await conn.run("whoami", check=True)
    return str(result.stdout).strip()


class BlockingPrompter(FakePrompter):
    """Answers like ``FakePrompter`` except ``block`` (a prompt kind), which never returns."""

    def __init__(self, block: str, **scripts: Any) -> None:
        super().__init__(**scripts)
        self.block = block
        self.asked = asyncio.Event()
        self.blocked_hop: HopRef | None = None

    async def _wait(self, hop: HopRef) -> Any:
        self.blocked_hop = hop
        self.asked.set()
        await asyncio.Event().wait()

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool:
        if self.block == "hostkey_new":
            return bool(await self._wait(hop))
        return await super().hostkey_new(hop, key_type, fingerprint)

    async def password(self, hop: HopRef, user: str, retry: bool) -> str:
        if self.block == "password":
            return str(await self._wait(hop))
        return await super().password(hop, user, retry)


def established_to(port: int) -> int:
    """ESTABLISHED TCP connections from this container to any host on ``port``."""
    count = 0
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if fields[3] == "01" and int(fields[2].rsplit(":", 1)[1], 16) == port:
                count += 1
    return count


async def wait_for_established(port: int, expected: int, seconds: float = 5.0) -> int:
    """Poll until ``established_to(port) == expected``; return the last count."""
    deadline = time.monotonic() + seconds
    while True:
        now = established_to(port)
        if now == expected or time.monotonic() > deadline:
            return now
        await asyncio.sleep(0.1)
