"""Fixtures for package 5's unit tests; fakes are in ``fakes.py``, the live server in
``live.py``.

- ``db``: a migrated SQLite database in a temp dir; ``connections``, ``settings``, ``events``:
  the real repositories on it.
- ``saved``: one saved connection (``web-prod``).
- ``connector``: a ``FakeConnector`` that knows ``saved``.
- ``clock``: a ``FakeClock``.
- ``make_registry(**limits)``: a ``MemorySessionRegistry`` over all of the above; closed at
  teardown.
- ``live``: the real app (``create_app``) served by uvicorn on a loopback socket, with a
  ``FakeConnector`` in its registry, one saved connection and an app token (``Live``).
  ``live_with(**limits)`` builds one with other ``Limits``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from portkey.app import create_app, services_of
from portkey.config import Config, Limits
from portkey.models import ConnectionDraft, ConnectionRecord
from portkey.services import Services
from portkey.sessions.registry import MemorySessionRegistry
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.settings import SqliteSettingsRepo
from tests.unit.sessions.fakes import FakeChannel, FakeClock, FakeConnector
from tests.unit.sessions.live import TermClient, live_server


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "portkey.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def connections(db: Database) -> SqliteConnectionsRepo:
    return SqliteConnectionsRepo(db, Limits())


@pytest.fixture
def settings(db: Database) -> SqliteSettingsRepo:
    return SqliteSettingsRepo(db)


@pytest.fixture
def events(db: Database) -> SqliteEventLog:
    return SqliteEventLog(db)


@pytest.fixture
def saved(connections: SqliteConnectionsRepo) -> ConnectionRecord:
    return connections.create(
        ConnectionDraft(
            name="web-prod", host="web.example.test", username="john", auth_method="key"
        )
    )


@pytest.fixture
def connector(saved: ConnectionRecord) -> FakeConnector:
    fake = FakeConnector()
    fake.known[saved.id] = saved.name
    return fake


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def make_registry(
    connector: FakeConnector,
    connections: SqliteConnectionsRepo,
    settings: SqliteSettingsRepo,
    events: SqliteEventLog,
    clock: FakeClock,
) -> AsyncIterator[Callable[..., MemorySessionRegistry]]:
    built: list[MemorySessionRegistry] = []

    def make(reap_interval: float = 3600.0, **limits: Any) -> MemorySessionRegistry:
        registry = MemorySessionRegistry(
            connector,
            connections,
            settings,
            events,
            Limits(**{"output_batch_seconds": 0.001, **limits}),
            clock=clock,
            reap_interval=reap_interval,
        )
        built.append(registry)
        return registry

    yield make
    for registry in built:
        await registry.aclose()


@dataclass
class Live:
    port: int
    token: str
    services: Services
    connector: FakeConnector
    conn: ConnectionRecord

    def client(self, **options: Any) -> contextlib.AbstractAsyncContextManager[TermClient]:
        return TermClient.connect(self.port, self.token, **options)

    def open_msg(self, **extra: Any) -> dict[str, Any]:
        return {"type": "open", "connection_id": self.conn.id, "cols": 80, "rows": 24, **extra}

    def channel(self, index: int = -1) -> FakeChannel:
        return self.connector.chains[index].channel


@pytest.fixture
async def live_with(config: Config) -> AsyncIterator[Callable[..., Any]]:
    stack = contextlib.AsyncExitStack()

    async def make(**limits: Any) -> Live:
        app = create_app(replace(config, limits=Limits(**limits)))
        port = await stack.enter_async_context(live_server(app))
        services = services_of(app)
        conn = await asyncio.to_thread(
            services.connections.create,
            ConnectionDraft(
                name="web-prod", host="web.example.test", username="john", auth_method="key"
            ),
        )
        connector = FakeConnector()
        connector.known[conn.id] = conn.name
        services.sessions.connector = connector
        issued = await asyncio.to_thread(services.tokens.issue)
        return Live(port, issued.token, services, connector, conn)

    async with stack:
        yield make


@pytest.fixture
async def live(live_with: Callable[..., Any]) -> Live:
    result: Live = await live_with(output_batch_seconds=0.002)
    return result
