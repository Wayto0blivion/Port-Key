"""Fixtures for package 1 (security) tests.

- ``fast_scrypt`` (autouse): drops scrypt's cost so the suite stays quick; the real parameters
  are checked in test_auth.py.
- ``db``, ``wall``, ``mono``, ``tokens``, ``auth``: a migrated temp database, fake wall and
  monotonic clocks, and the token store / password auth built on them.
- ``http_call`` / ``ws_call``: drive an ASGI app with exact raw headers (duplicates, missing
  Host, ...), which an HTTP client library would normalise away.
- ``setup_code``: the first-visit setup code the app (the root ``client`` fixture) printed at
  startup, read from the captured log the way the owner reads ``docker compose logs``
  (``setup_log.codes_in_log``).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from portkey.security import auth as auth_module
from portkey.security.auth import PasswordAuth
from portkey.security.tokens import SqliteTokenStore
from portkey.store.db import Database
from tests.unit.security.setup_log import codes_in_log

TTL = 30 * 24 * 3600.0
ALLOWED_ORIGIN = "http://localhost:8822"
ALLOWED_HOST = "localhost:8822"
START_WALL = 1_800_000_000.0  # 2027-01-15


class FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "SCRYPT_N", 2**10)


@pytest.fixture
def setup_code(client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture) -> str:
    # ``client`` started the app in this test's setup phase; its records are still live here.
    codes = codes_in_log(caplog.records)
    assert len(codes) == 1, codes
    return codes[0]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "security.db")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def wall() -> FakeClock:
    return FakeClock(START_WALL)


@pytest.fixture
def mono() -> FakeClock:
    return FakeClock(1000.0)


@pytest.fixture
def tokens(db: Database, wall: FakeClock) -> SqliteTokenStore:
    return SqliteTokenStore(db, TTL, clock=wall)


@pytest.fixture
def auth(db: Database, tokens: SqliteTokenStore, mono: FakeClock) -> PasswordAuth:
    return PasswordAuth(db, tokens, clock=mono)


# --- raw ASGI drivers ----------------------------------------------------------------------------

HeaderList = list[tuple[str, str]]


def _scope(kind: str, path: str, headers: HeaderList, method: str = "GET") -> Scope:
    scope: Scope = {
        "type": kind,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws" if kind == "websocket" else "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("172.17.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    if kind == "http":
        scope["method"] = method
    else:
        scope["subprotocols"] = []
    return scope


class HttpResult:
    def __init__(self, status: int, headers: Headers, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


HttpCall = Callable[..., Awaitable[HttpResult]]
WsCall = Callable[..., Awaitable[list[Message]]]


async def call_http(
    app: ASGIApp, method: str, path: str, headers: HeaderList, body: bytes = b""
) -> HttpResult:
    sent: list[Message] = []
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope("http", path, headers, method), receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return HttpResult(start["status"], Headers(raw=list(start["headers"])), data)


async def call_ws(app: ASGIApp, path: str, headers: HeaderList) -> list[Message]:
    """Open a WebSocket handshake and disconnect right after; returns what the app sent."""
    inbox: list[Message] = [{"type": "websocket.connect"}]
    sent: list[Message] = []

    async def receive() -> Message:
        if inbox:
            return inbox.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope("websocket", path, headers), receive, send)
    return sent


@pytest.fixture
def http_call() -> HttpCall:
    return call_http


@pytest.fixture
def ws_call() -> WsCall:
    return call_ws


class Spy:
    """Downstream ASGI app that records whether it ran; answers 200 / accepts the socket."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
            return
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def spy() -> Spy:
    return Spy()
