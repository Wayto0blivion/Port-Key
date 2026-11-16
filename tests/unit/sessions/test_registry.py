"""MemorySessionRegistry: create, the 32-session cap, kill, the reaper on a fake clock."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from portkey.errors import NotFound
from portkey.models import ConnectionRecord
from portkey.sessions.registry import MemorySessionRegistry
from portkey.ssh.errors import SessionGone, TooManySessions, UnknownConnection
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.events import SqliteEventLog
from portkey.store.settings import SqliteSettingsRepo
from tests.fixtures.prompter import FakePrompter
from tests.unit.sessions.fakes import FakeClock, FakeConnector, RecordingSink, settle

MakeRegistry = Callable[..., MemorySessionRegistry]


async def open_one(registry: MemorySessionRegistry, conn: ConnectionRecord) -> str:
    session = await registry.create(conn.id, 80, 24, FakePrompter(), False)
    return session.session_id


async def until(predicate: Callable[[], bool], seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.005)


# --- create --------------------------------------------------------------------------------


async def test_create_registers_and_records_the_use(
    make_registry: MakeRegistry,
    saved: ConnectionRecord,
    connections: SqliteConnectionsRepo,
    events: SqliteEventLog,
) -> None:
    registry = make_registry()
    session_id = await open_one(registry, saved)
    assert session_id.startswith("s_")
    assert len(session_id) > 20
    [summary] = registry.list_all()
    assert (summary.session_id, summary.connection_id) == (session_id, saved.id)
    assert summary.attached is False  # not attached until the socket attaches
    await until(lambda: connections.get(saved.id).use_count == 1)
    await until(lambda: any(e.kind == "session_opened" for e in events.recent()))
    opened = next(e for e in events.recent() if e.kind == "session_opened")
    assert opened.detail == {"host": "web.example.test", "port": 22}
    assert registry.get(session_id).connection is not None


async def test_unknown_connection_and_failed_shell(
    make_registry: MakeRegistry, saved: ConnectionRecord, connector: FakeConnector
) -> None:
    registry = make_registry(max_sessions=1)
    with pytest.raises(UnknownConnection):
        await registry.create("9e8d7c6b-0000-4000-8000-000000000000", 80, 24, FakePrompter(), False)
    original = connector.connect

    async def connect_then_break(hops, prompter):  # type: ignore[no-untyped-def]
        chain = await original(hops, prompter)
        chain.conn.fail = OSError("no shell")
        return chain

    connector.connect = connect_then_break  # type: ignore[method-assign]
    with pytest.raises(Exception, match="would not start a shell"):
        await open_one(registry, saved)
    assert connector.chains[-1].close_calls == 1
    assert registry.list_all() == []
    connector.connect = original  # type: ignore[method-assign]
    await open_one(registry, saved)  # the slot was given back


async def test_the_cap_counts_sessions_and_attempts_in_flight(
    make_registry: MakeRegistry, saved: ConnectionRecord, connector: FakeConnector
) -> None:
    registry = make_registry(max_sessions=2)
    await open_one(registry, saved)
    connector.block = asyncio.Event()
    connector.connecting.clear()
    connecting = asyncio.create_task(open_one(registry, saved))
    await until(connector.connecting.is_set)
    with pytest.raises(TooManySessions) as full:
        await open_one(registry, saved)
    assert full.value.close_code == 4429
    assert full.value.code == "too_many_sessions"
    connector.block.set()
    await connecting
    assert len(registry.list_all()) == 2


async def test_thirty_two_sessions_by_default(
    make_registry: MakeRegistry, saved: ConnectionRecord
) -> None:
    registry = make_registry()
    for _ in range(32):
        await open_one(registry, saved)
    with pytest.raises(TooManySessions):
        await open_one(registry, saved)
    await registry.kill(registry.list_all()[0].session_id)
    await open_one(registry, saved)


async def test_ended_sessions_do_not_count(
    make_registry: MakeRegistry, saved: ConnectionRecord, connector: FakeConnector
) -> None:
    registry = make_registry(max_sessions=1)
    session_id = await open_one(registry, saved)
    connector.chains[-1].channel.finish(status=0)  # exits while detached: kept for a reattach
    assert registry.list_all() == []
    assert registry.get(session_id).ended
    await open_one(registry, saved)


# --- kill ----------------------------------------------------------------------------------


async def test_kill_closes_every_hop_and_records_it(
    make_registry: MakeRegistry,
    saved: ConnectionRecord,
    connector: FakeConnector,
    events: SqliteEventLog,
) -> None:
    registry = make_registry()
    session_id = await open_one(registry, saved)
    sink = RecordingSink()
    await registry.get(session_id).attach(sink, None)
    await registry.kill(session_id)
    assert connector.chains[-1].close_calls == 1
    with pytest.raises(SessionGone):
        registry.get(session_id)
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert sink.first("exit") == (None, None, "killed")
    await until(lambda: any(e.kind == "session_closed" for e in events.recent()))
    closed = next(e for e in events.recent() if e.kind == "session_closed")
    assert closed.detail == {"outcome": "killed"}
    with pytest.raises(NotFound) as gone:
        await registry.kill(session_id)
    assert gone.value.code == "session_gone"
    assert gone.value.status == 404


async def test_a_delivered_exit_removes_the_session(
    make_registry: MakeRegistry, saved: ConnectionRecord, connector: FakeConnector
) -> None:
    registry = make_registry()
    session_id = await open_one(registry, saved)
    sink = RecordingSink()
    await registry.get(session_id).attach(sink, None)
    connector.chains[-1].channel.finish(status=7)
    await sink.wait_for(lambda: "exit" in sink.kinds())
    await settle()
    with pytest.raises(SessionGone):
        registry.get(session_id)


# --- reaper --------------------------------------------------------------------------------


async def test_reaper_closes_after_the_grace_from_settings(
    make_registry: MakeRegistry,
    saved: ConnectionRecord,
    connector: FakeConnector,
    settings: SqliteSettingsRepo,
    clock: FakeClock,
) -> None:
    settings.update({"detach_grace": 60})
    registry = make_registry()
    session_id = await open_one(registry, saved)
    session = registry.get(session_id)
    sink = RecordingSink()
    await session.attach(sink, None)
    clock.advance(3600)
    assert await registry.reap_once() == []  # attached: never reaped
    session.detach(sink)
    clock.advance(59.9)
    assert await registry.reap_once() == []
    assert connector.chains[-1].close_calls == 0
    clock.advance(0.1)
    assert await registry.reap_once() == [session_id]
    assert connector.chains[-1].close_calls == 1
    assert registry.list_all() == []


async def test_reattaching_restarts_the_grace(
    make_registry: MakeRegistry, saved: ConnectionRecord, clock: FakeClock
) -> None:
    registry = make_registry()  # default grace: 15 minutes
    session = registry.get(await open_one(registry, saved))
    clock.advance(899)  # never attached: the grace runs from the open
    sink = RecordingSink()
    await session.attach(sink, None)
    clock.advance(10)
    session.detach(sink)
    clock.advance(899)
    assert await registry.reap_once() == []
    clock.advance(1)
    assert await registry.reap_once() == [session.session_id]


async def test_a_session_reattached_while_the_reaper_closes_another_is_kept(
    make_registry: MakeRegistry,
    saved: ConnectionRecord,
    connector: FakeConnector,
    clock: FakeClock,
) -> None:
    # Review finding: the reaper listed the expired sessions once, then closed them one by one
    # (seconds per hop when a host does not answer); one reattached meanwhile was killed anyway.
    registry = make_registry()
    first = await open_one(registry, saved)
    first_chain = connector.chains[-1]
    second = await open_one(registry, saved)
    release = asyncio.Event()
    closing = asyncio.Event()

    async def slow_close() -> None:
        first_chain.close_calls += 1
        closing.set()
        await release.wait()

    first_chain.close = slow_close  # type: ignore[method-assign]
    clock.advance(900)
    reaping = asyncio.create_task(registry.reap_once())
    await asyncio.wait_for(closing.wait(), 2)
    sink = RecordingSink()
    await registry.get(second).attach(sink, None)  # the owner reopened the page
    release.set()
    assert await asyncio.wait_for(reaping, 2) == [first]
    assert registry.get(second).exit is None
    await settle()
    assert "exit" not in sink.kinds()


async def test_ended_detached_sessions_are_reaped_too(
    make_registry: MakeRegistry,
    saved: ConnectionRecord,
    connector: FakeConnector,
    clock: FakeClock,
) -> None:
    registry = make_registry()
    session_id = await open_one(registry, saved)
    connector.chains[-1].channel.finish(exc=ConnectionResetError())
    clock.advance(900)
    assert await registry.reap_once() == [session_id]
    with pytest.raises(SessionGone):
        registry.get(session_id)


async def test_the_reaper_runs_in_the_background(
    make_registry: MakeRegistry, saved: ConnectionRecord, clock: FakeClock
) -> None:
    registry = make_registry(reap_interval=0.01)
    await registry.start()
    await registry.start()  # once only
    await open_one(registry, saved)
    clock.advance(900)
    await until(lambda: registry.list_all() == [])


async def test_aclose_closes_everything_and_is_safe_twice(
    make_registry: MakeRegistry, saved: ConnectionRecord, connector: FakeConnector
) -> None:
    registry = make_registry()
    await registry.aclose()  # nothing open, never started
    await registry.start()
    await open_one(registry, saved)
    await open_one(registry, saved)
    await registry.aclose()
    await registry.aclose()
    assert [c.close_calls for c in connector.chains] == [1, 1]
    assert registry.list_all() == []


async def test_connection_of_falls_back_to_the_record_from_open_time(
    make_registry: MakeRegistry, saved: ConnectionRecord, connections: SqliteConnectionsRepo
) -> None:
    registry = make_registry()
    session = registry.get(await open_one(registry, saved))
    await until(lambda: session.connection is not None)
    connections.update(saved.id, {"color": "red"})
    current = await registry.connection_of(session)
    assert current is not None
    assert current.color == "red"
    connections.delete(saved.id)
    kept = await registry.connection_of(session)
    assert kept is not None
    assert kept.name == "web-prod"
