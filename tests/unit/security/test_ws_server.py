"""A real uvicorn server on container loopback: a refused handshake is an HTTP 403 upgrade
failure, exactly what a browser's ``new WebSocket()`` sees. Nothing leaves the container."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
from websockets.typing import Origin

from portkey.app import create_app
from portkey.config import Config
from portkey.security.hostguard import HostGuardMiddleware


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[Any, socket.socket], Any]]:
    servers: list[tuple[uvicorn.Server, asyncio.Task[None]]] = []

    async def start(app: Any, sock: socket.socket) -> None:
        config = uvicorn.Config(app, lifespan="off", log_level="warning", ws="auto")
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        servers.append((server, task))
        for _ in range(500):
            if server.started:
                return
            if task.done():
                task.result()  # surface a startup failure
            await asyncio.sleep(0.01)
        pytest.fail("uvicorn did not start")

    yield start
    for server, task in servers:
        server.should_exit = True
        await asyncio.wait_for(task, 5)


def _listener() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock, f"127.0.0.1:{sock.getsockname()[1]}"


async def _handshake_status(host: str, origin: str) -> int:
    try:
        async with connect(
            f"ws://{host}/ws/terminal", origin=Origin(origin), proxy=None, open_timeout=5
        ):
            return 101
    except InvalidStatus as refused:
        return refused.response.status_code


async def test_real_server_refuses_cross_origin_with_403(serve: Any, config: Config) -> None:
    sock, host = _listener()
    await serve(create_app(replace(config, allowed_hosts=(host,))), sock)
    assert await _handshake_status(host, "http://localhost:3000") == 403
    assert await _handshake_status(host, "http://evil.example") == 403


async def test_real_server_accepts_own_origin(serve: Any, spy: Any) -> None:
    sock, host = _listener()
    await serve(HostGuardMiddleware(spy, Config(allowed_hosts=(host,))), sock)
    assert await _handshake_status(host, f"http://{host}") == 101
    assert await _handshake_status(host, "http://localhost:3000") == 403
    assert spy.calls == 1
