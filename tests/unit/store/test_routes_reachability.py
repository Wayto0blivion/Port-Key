"""POST /api/connections/reachability: TCP connect, skipped behind a jump, unknown ids left out.

The "up" target is a listener inside the test container on loopback; nothing leaves it.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from portkey.api import routes_connections


@pytest.fixture
async def listener() -> AsyncIterator[int]:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


async def _create(api: httpx.AsyncClient, name: str, **fields: Any) -> str:
    body = {"name": name, "host": "127.0.0.1", "username": "john", "auth_method": "key"}
    body.update(fields)
    r = await api.post("/api/connections", json=body)
    assert r.status_code == 201, r.text
    conn_id: str = r.json()["id"]
    return conn_id


async def test_up_down_skipped_and_unknown(authed_client: httpx.AsyncClient, listener: int) -> None:
    up = await _create(authed_client, "local-up", port=listener)
    down = await _create(authed_client, "local-down", port=_closed_port())
    behind = await _create(authed_client, "behind-jump", host="192.168.10.42", jump_id=up)
    unknown = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
    r = await authed_client.post(
        "/api/connections/reachability", json={"ids": [behind, unknown, down, up, up]}
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["id"] for x in results] == [behind, down, up]
    by_id = {x["id"]: x for x in results}
    assert by_id[behind] == {"id": behind, "state": "skipped"}
    assert by_id[down] == {"id": down, "state": "down"}
    assert by_id[up]["state"] == "up"
    assert isinstance(by_id[up]["ms"], int)


async def test_too_many_ids_rejected(authed_client: httpx.AsyncClient) -> None:
    ids = ["9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"] * 501
    r = await authed_client.post("/api/connections/reachability", json={"ids": ids})
    assert r.status_code == 422


async def test_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr(routes_connections.asyncio, "open_connection", hang)
    assert await routes_connections._probe("192.168.10.42", 22, 0.05) is None


async def test_probe_handles_bad_names() -> None:
    assert await routes_connections._probe("bad..name..invalid", 22, 1.0) is None


async def test_concurrency_is_bounded(
    monkeypatch: pytest.MonkeyPatch, authed_client: httpx.AsyncClient
) -> None:
    running = 0
    peak = 0

    async def fake_probe(host: str, port: int, limit_s: float) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return 1

    monkeypatch.setattr(routes_connections, "REACH_CONCURRENCY", 4)
    monkeypatch.setattr(routes_connections, "_probe", fake_probe)
    ids = [await _create(authed_client, f"host-{i}") for i in range(12)]
    r = await authed_client.post("/api/connections/reachability", json={"ids": ids})
    assert [x["state"] for x in r.json()["results"]] == ["up"] * 12
    assert peak == 4
