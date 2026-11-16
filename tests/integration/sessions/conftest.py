"""Fixtures for package 5's integration tests: the real app (real SSH core, real key provider
over the 0777 fixture .ssh copy) served by uvicorn on a loopback socket inside the integration
container, driven through /ws/terminal against the sshd test bed.

- ``app_live``: ``AppLive`` with ``port``, ``token``, ``services``, ``add(name, host, user,
  ...)`` to save a connection, ``client()`` for an authenticated ``TermClient`` and
  ``open_msg(conn)``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from portkey.app import create_app, services_of
from portkey.config import Config
from portkey.models import ConnectionDraft, ConnectionRecord
from portkey.services import Services
from portkey.sessions.terminal import TerminalSession
from tests.unit.sessions.live import TermClient, live_server


@dataclass
class AppLive:
    port: int
    token: str
    services: Services

    def client(self, **options: Any) -> contextlib.AbstractAsyncContextManager[TermClient]:
        return TermClient.connect(self.port, self.token, **options)

    async def add(
        self,
        name: str,
        host: str,
        user: str,
        *,
        auth_method: str = "key",
        jump: ConnectionRecord | None = None,
    ) -> ConnectionRecord:
        draft = ConnectionDraft(
            name=name,
            host=host,
            username=user,
            auth_method=auth_method,  # type: ignore[arg-type]
            jump_id=jump.id if jump else None,
        )
        return await asyncio.to_thread(self.services.connections.create, draft)

    @staticmethod
    def open_msg(conn: ConnectionRecord, cols: int = 80, rows: int = 24) -> dict[str, Any]:
        return {"type": "open", "connection_id": conn.id, "cols": cols, "rows": rows}

    def session(self, session_id: str) -> TerminalSession:
        return self.services.sessions.get(session_id)


@pytest.fixture
async def app_live(portkey_config: Config) -> AsyncIterator[AppLive]:
    app = create_app(portkey_config)
    async with live_server(app) as port:
        services = services_of(app)
        issued = await asyncio.to_thread(services.auth.setup, "correct horse battery")
        yield AppLive(port, issued.token, services)
