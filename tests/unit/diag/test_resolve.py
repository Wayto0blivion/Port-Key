"""POST /api/diag/resolve and its helpers (package 7)."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from portkey.api import routes_diag


@pytest.fixture
async def listening_port() -> AsyncIterator[int]:
    """A TCP server on 127.0.0.1 that accepts and closes."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_up(authed_client: httpx.AsyncClient, listening_port: int) -> None:
    r = await authed_client.post(
        "/api/diag/resolve", json={"host": "127.0.0.1", "port": listening_port}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["host"] == "127.0.0.1"
    assert body["port"] == listening_port
    assert body["addresses"] == ["127.0.0.1"]
    assert body["resolve_error"] is None
    assert body["tcp"]["state"] == "up"
    assert isinstance(body["tcp"]["ms"], int)
    assert body["tcp"]["error"] is None


async def test_name_resolves(authed_client: httpx.AsyncClient, listening_port: int) -> None:
    body = (
        await authed_client.post(
            "/api/diag/resolve", json={"host": "localhost", "port": listening_port}
        )
    ).json()
    assert "127.0.0.1" in body["addresses"]
    assert len(body["addresses"]) == len(set(body["addresses"]))
    assert body["tcp"]["state"] == "up"


async def test_refused(authed_client: httpx.AsyncClient) -> None:
    port = closed_port()
    body = (
        await authed_client.post("/api/diag/resolve", json={"host": "127.0.0.1", "port": port})
    ).json()
    assert body["tcp"] == {"state": "down", "ms": None, "error": "refused"}


async def test_default_port_and_brackets(authed_client: httpx.AsyncClient) -> None:
    body = (await authed_client.post("/api/diag/resolve", json={"host": "[127.0.0.1]"})).json()
    assert body["host"] == "[127.0.0.1]"
    assert body["port"] == 22
    assert body["addresses"] == ["127.0.0.1"]


async def test_unknown_name_skips_tcp(
    authed_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_such_name(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", no_such_name)
    body = (
        await authed_client.post("/api/diag/resolve", json={"host": "parser-vm", "port": 22})
    ).json()
    assert body == {
        "host": "parser-vm",
        "port": 22,
        "addresses": [],
        "resolve_error": "Name or service not known",
        "tcp": {"state": "skipped", "ms": None, "error": None},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"host": "build box", "port": 22},
        {"host": "john@build-box"},
        {"host": "build-box/x"},
        {"host": ""},
        {"host": "build-box", "port": 0},
        {"host": "build-box", "port": 65536},
        {"host": "build-box", "extra": 1},
        {"hosts": ["build-box", "web-prod"]},
    ],
)
async def test_takes_exactly_one_valid_host(
    authed_client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    r = await authed_client.post("/api/diag/resolve", json=body)
    assert r.status_code == 422


async def test_resolver_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", slow)
    addresses, error = await routes_diag.resolve_host("build-box", 22, limit=0.05)
    assert addresses == []
    assert error is not None
    assert "resolver" in error


async def test_tcp_timeout_is_short(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(routes_diag.asyncio, "open_connection", hang)
    loop = asyncio.get_running_loop()
    started = loop.time()
    tcp = await routes_diag.tcp_check(["192.168.10.20", "192.168.10.21"], 22, limit=0.1)
    assert tcp.state == "down"
    assert tcp.error == "timeout"
    assert loop.time() - started < 1


async def test_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_route(*args: Any, **kwargs: Any) -> Any:
        raise OSError(113, "No route to host")

    monkeypatch.setattr(routes_diag.asyncio, "open_connection", no_route)
    tcp = await routes_diag.tcp_check(["10.8.0.1"], 22, limit=1)
    assert (tcp.state, tcp.error) == ("down", "unreachable")


async def test_tries_the_next_address_after_a_refusal(listening_port: int) -> None:
    # The server listens on 127.0.0.1 only, so 127.0.0.2 refuses.
    tcp = await routes_diag.tcp_check(["127.0.0.2", "127.0.0.1"], listening_port, limit=2)
    assert tcp.state == "up"


async def test_tries_at_most_a_few_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    tried: list[str] = []

    async def refuse(host: str, port: int) -> Any:
        tried.append(host)
        raise ConnectionRefusedError

    monkeypatch.setattr(routes_diag.asyncio, "open_connection", refuse)
    addresses = [f"192.168.10.{i}" for i in range(20, 30)]
    tcp = await routes_diag.tcp_check(addresses, 22, limit=1)
    assert (tcp.state, tcp.error) == ("down", "refused")
    assert tried == addresses[: routes_diag.MAX_TCP_ADDRESSES]


async def test_no_addresses_is_skipped() -> None:
    tcp = await routes_diag.tcp_check([], 22, limit=1)
    assert (tcp.state, tcp.ms, tcp.error) == ("skipped", None, None)
