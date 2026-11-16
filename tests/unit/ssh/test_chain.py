"""Jump-chain planning against the real store, and connect/close with a fake hop connector."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import portkey.ssh.chain as chain_mod
from portkey.config import Limits
from portkey.models import ConnectionDraft, ConnectionRecord, HopRef, HopTarget
from portkey.ssh._hop import HopDeps, connect_hop
from portkey.ssh.chain import ChainConnector, OpenChain
from portkey.ssh.errors import (
    ConnectionRefused,
    HostKeyChanged,
    JumpFailed,
    KeyUnreadable,
    ProtocolError,
    UnknownConnection,
)
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.hostkeys import SqliteHostKeyStore
from portkey.store.settings import SqliteSettingsRepo
from tests.fixtures.prompter import FakePrompter
from tests.unit.ssh.fakes import FakeKeys, StubKnownHosts, hop, target


@pytest.fixture
def keys() -> FakeKeys:
    return FakeKeys()


@pytest.fixture
def connector(
    db: Database, store: SqliteHostKeyStore, events: SqliteEventLog, keys: FakeKeys
) -> ChainConnector:
    limits = Limits()
    return ChainConnector(
        SqliteConnectionsRepo(db, limits),
        keys,
        store,
        StubKnownHosts(),
        events,
        SqliteSettingsRepo(db),
        limits,
        vault=keys.vault,
    )


def save(c: ChainConnector, name: str, **kw: Any) -> ConnectionRecord:
    values: dict[str, Any] = {"host": f"{name}.example.test", "username": "john"}
    values.update(kw)
    values.setdefault("auth_method", "key")
    return c.connections.create(ConnectionDraft(name=name, **values))


# --- plan -----------------------------------------------------------------------------------------


def test_plan_resolves_the_default_key(connector: ChainConnector, keys: FakeKeys) -> None:
    rec = keys.add("id_ed25519")
    conn = save(connector, "web-prod", port=2222)
    [only] = connector.plan(conn.id)
    assert only.ref == HopRef(0, 1, conn.id, "web-prod", "web-prod.example.test", 2222, "target")
    assert (only.username, only.auth_method, only.key_fp) == ("john", "key", rec.fp)
    assert (only.allow_key, only.allow_password, only.allow_kbdint) == (True, True, True)


def test_plan_password_connection_never_offers_a_key(connector: ChainConnector) -> None:
    conn = save(connector, "nas", auth_method="password")
    [only] = connector.plan(conn.id)
    assert (only.auth_method, only.key_fp, only.allow_key) == ("password", None, False)


def test_plan_chain_outermost_first_with_each_hops_own_settings(
    connector: ChainConnector, keys: FakeKeys
) -> None:
    keys.add("id_ed25519")
    other = keys.add("id_other")
    outer = save(connector, "gw", username="admin", auth_method="password")
    mid = save(connector, "build-box", jump_id=outer.id, key_fp=other.fp)
    inner = save(connector, "web-prod", jump_id=mid.id)
    hops = connector.plan(inner.id, force_password=True)
    assert [(h.ref.index, h.ref.count, h.ref.role, h.ref.name) for h in hops] == [
        (0, 3, "jump", "gw"),
        (1, 3, "jump", "build-box"),
        (2, 3, "target", "web-prod"),
    ]
    assert [h.username for h in hops] == ["admin", "john", "john"]
    assert hops[1].key_fp == other.fp
    # force_password applies to the target only
    assert [h.allow_key for h in hops] == [False, True, False]


def test_plan_missing_key_is_key_unreadable(connector: ChainConnector) -> None:
    conn = save(connector, "web-prod")
    with pytest.raises(KeyUnreadable) as missing:
        connector.plan(conn.id)
    assert missing.value.hop is not None
    assert missing.value.hop.name == "web-prod"
    [forced] = connector.plan(conn.id, force_password=True)
    assert forced.allow_key is False


def test_plan_unknown_connection(connector: ChainConnector) -> None:
    with pytest.raises(UnknownConnection):
        connector.plan("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a")


def test_plan_new_after_a_saved_jump(connector: ChainConnector, keys: FakeKeys) -> None:
    rec = keys.add("id_ed25519")
    jump = save(connector, "build-box")
    draft = target(hop(5, 9, name="new-host", connection_id=None), key_fp=None)
    hops = connector.plan_new(jump.id, draft)
    assert [(h.ref.index, h.ref.count, h.ref.role) for h in hops] == [
        (0, 2, "jump"),
        (1, 2, "target"),
    ]
    assert hops[1].ref.connection_id is None
    assert hops[1].key_fp == rec.fp
    [alone] = connector.plan_new(None, target(auth_method="password", key_fp=None))
    assert (alone.ref.index, alone.ref.count, alone.key_fp) == (0, 1, None)


def test_plan_new_unknown_jump_and_no_key(connector: ChainConnector) -> None:
    with pytest.raises(UnknownConnection):
        connector.plan_new("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", target())
    with pytest.raises(KeyUnreadable):
        connector.plan_new(None, target(key_fp=None))


# --- connect --------------------------------------------------------------------------------------


class FakeConn:
    def __init__(self, name: str, log: list[tuple[str, str]], slow: bool = False) -> None:
        self.name, self.log, self.slow = name, log, slow

    def close(self) -> None:
        self.log.append(("close", self.name))

    async def wait_closed(self) -> None:
        if self.slow:
            await asyncio.sleep(10)

    def abort(self) -> None:
        self.log.append(("abort", self.name))


class FakeHops:
    """Stands in for ``_hop.connect_hop``; ``fail`` maps a hop index to an exception."""

    def __init__(self, fail: dict[int, BaseException] | None = None, block: int = -1) -> None:
        self.fail = fail or {}
        self.block = block
        self.log: list[tuple[str, str]] = []
        self.tunnels: list[str | None] = []
        self.deps: list[HopDeps] = []
        self.started = asyncio.Event()

    async def __call__(self, t: HopTarget, tunnel: Any, prompter: Any, deps: HopDeps) -> FakeConn:
        self.tunnels.append(tunnel.name if tunnel else None)
        self.deps.append(deps)
        await prompter.status("connecting", t.ref)
        if t.ref.index == self.block:
            self.started.set()
            await asyncio.Event().wait()
        if t.ref.index in self.fail:
            raise self.fail[t.ref.index]
        return FakeConn(t.ref.name, self.log)


def chain_of(n: int) -> list[HopTarget]:
    return [target(hop(i, n, name=f"h{i}")) for i in range(n)]


async def test_connect_tunnels_each_hop_through_the_previous(connector: ChainConnector) -> None:
    fake = FakeHops()
    connector.connect_hop = fake
    prompter = FakePrompter()
    chain = await connector.connect(chain_of(3), prompter)
    assert isinstance(chain, OpenChain)
    assert fake.tunnels == [None, "h0", "h1"]
    assert prompter.phases() == [
        (0, "connecting"),
        (1, "connecting"),
        (2, "connecting"),
        (2, "ready"),
    ]
    assert [h.name for h in chain.hops] == ["h0", "h1", "h2"]
    assert fake.deps[0].passphrase_ttl == 8 * 3600
    await chain.close()
    await chain.close()
    assert fake.log == [("close", "h2"), ("close", "h1"), ("close", "h0")]


async def test_connect_enforces_the_depth_limit_again(connector: ChainConnector) -> None:
    fake = FakeHops()
    connector.connect_hop = fake
    with pytest.raises(ProtocolError, match="at most 3 hosts"):
        await connector.connect(chain_of(5), FakePrompter())
    assert fake.tunnels == []


async def test_a_failing_jump_is_wrapped_and_earlier_hops_aborted(
    connector: ChainConnector,
) -> None:
    refused = ConnectionRefused("h1.example.test refused the connection on port 22.")
    fake = FakeHops(fail={1: refused})
    connector.connect_hop = fake
    with pytest.raises(JumpFailed) as failed:
        await connector.connect(chain_of(3), FakePrompter())
    assert failed.value.extra["cause"] == "refused"
    assert failed.value.hop is not None
    assert failed.value.hop.index == 1
    assert failed.value.message == refused.message
    assert fake.log == [("abort", "h0")]


async def test_a_jump_host_key_change_keeps_its_code(connector: ChainConnector) -> None:
    connector.connect_hop = FakeHops(fail={0: HostKeyChanged("changed")})
    with pytest.raises(HostKeyChanged) as changed:
        await connector.connect(chain_of(2), FakePrompter())
    assert changed.value.hop is not None
    assert changed.value.hop.role == "jump"


async def test_a_failing_target_is_not_wrapped(connector: ChainConnector) -> None:
    fake = FakeHops(fail={1: ConnectionRefused("refused")})
    connector.connect_hop = fake
    with pytest.raises(ConnectionRefused):
        await connector.connect(chain_of(2), FakePrompter())
    assert fake.log == [("abort", "h0")]


async def test_cancelling_connect_aborts_the_hops_already_open(
    connector: ChainConnector,
) -> None:
    fake = FakeHops(block=2)
    connector.connect_hop = fake
    task = asyncio.create_task(connector.connect(chain_of(3), FakePrompter()))
    await asyncio.wait_for(fake.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.log == [("abort", "h1"), ("abort", "h0")]


async def test_close_aborts_a_hop_that_does_not_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain_mod, "CLOSE_TIMEOUT", 0.05)
    log: list[tuple[str, str]] = []
    conns: list[Any] = [FakeConn("h0", log), FakeConn("h1", log, slow=True)]
    chain = OpenChain(conns, [hop(0, 2), hop(1, 2)])
    await chain.close()
    assert log == [("close", "h1"), ("abort", "h1"), ("close", "h0")]


def test_connect_hop_is_the_real_one_by_default(connector: ChainConnector) -> None:
    assert connector.connect_hop is connect_hop
