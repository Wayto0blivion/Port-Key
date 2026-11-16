"""A real uvicorn server in the test's event loop, and a /ws/terminal client (package 5).

The server listens on a 127.0.0.1 socket inside the test container (nothing is published on
the host) with the same WebSocket settings as ``python -m portkey`` (``ws_max_size`` 64 KB,
uvicorn's own flow control), so the 1009 frame cap and backpressure are the real ones. The
client connects there but sends ``Host: localhost:8822`` and the matching ``Origin``, which the
app's host guard allows.

- ``live_server(app)``: async context manager yielding the port; runs the app's lifespan.
- ``TermClient.connect(port, token)``: an authenticated client. ``json()`` returns the next
  text message (binary frames in between are appended to ``output``), ``until(type)`` skips to
  a message type, ``read_until(pattern)`` waits for output, ``close_code()`` waits for the
  close. ``received`` counts output bytes, for the next ``attach``'s ``since``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

ORIGIN = "http://localhost:8822"
URL = "ws://localhost:8822/ws/terminal"
MAX_FRAME = 64 * 1024


@contextlib.asynccontextmanager
async def live_server(app: Any) -> AsyncIterator[int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    config = uvicorn.Config(
        app,
        log_level="warning",
        lifespan="on",
        ws_max_size=MAX_FRAME,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn stopped during startup")
            await asyncio.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()


class TermClient:
    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.output = bytearray()
        self.offset: int | None = None  # ready.offset
        self.frames = 0
        self._texts: list[str] = []  # text messages that arrived during read_until

    async def _receive(self) -> str | bytes:
        """The next message; binary frames are also added to ``output``."""
        message = self._texts.pop(0) if self._texts else await self.ws.recv()
        if isinstance(message, bytes):
            self.output += message
            self.frames += 1
        return message

    @classmethod
    @contextlib.asynccontextmanager
    async def connect(
        cls, port: int, token: str | None = None, **options: Any
    ) -> AsyncIterator[TermClient]:
        async with connect(
            URL,
            origin=ORIGIN,  # type: ignore[arg-type]
            host="127.0.0.1",
            port=port,
            open_timeout=5,
            ping_interval=None,
            max_size=None,
            **options,
        ) as ws:
            client = cls(ws)
            if token is not None:
                await client.send({"type": "auth", "token": token})
            yield client

    @property
    def received(self) -> int:
        """``since`` for a reattach: the ready offset plus the bytes received after it."""
        return (self.offset or 0) + len(self.output)

    async def send(self, msg: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(msg))

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send(data)

    async def json(self, seconds: float = 5.0) -> dict[str, Any]:
        async def next_text() -> dict[str, Any]:
            while True:
                message = await self._receive()
                if isinstance(message, bytes):
                    continue
                data: dict[str, Any] = json.loads(message)
                if data.get("type") == "ready":
                    self.offset = data["offset"]
                return data

        return await asyncio.wait_for(next_text(), seconds)

    async def until(self, kind: str, seconds: float = 5.0) -> dict[str, Any]:
        """Skip ``status`` (and anything else) until a message of type ``kind``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            msg = await self.json(max(0.01, deadline - loop.time()))
            if msg["type"] == kind:
                return msg
            if msg["type"] == "error" and kind != "error":
                raise AssertionError(f"expected {kind}, got error {msg}")

    async def read_until(self, pattern: bytes, seconds: float = 5.0) -> bytes:
        """Receive until ``pattern`` is in ``output``; text messages are kept for ``json``."""

        async def wait() -> None:
            while pattern not in self.output:
                message = await self.ws.recv()
                if isinstance(message, bytes):
                    self.output += message
                    self.frames += 1
                else:
                    self._texts.append(message)

        await asyncio.wait_for(wait(), seconds)
        return bytes(self.output)

    async def close_code(self, seconds: float = 5.0) -> int | None:
        """Read until the server closes; the close code it sent."""

        async def drain() -> int | None:
            try:
                while True:
                    await self._receive()
            except ConnectionClosed as closed:
                return closed.rcvd.code if closed.rcvd is not None else None

        return await asyncio.wait_for(drain(), seconds)
