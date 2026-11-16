"""/ws/terminal (package 5). Contract: docs/ws-protocol.md "/ws/terminal".

Flow: ``ws_common.authenticate`` -> first message ``open`` (create a session through
``services.sessions.create`` with a ``WsPrompter``) or ``attach`` (``SessionGone`` -> 4410) ->
``ready`` -> binary output frames. The receive loop runs for the whole life of the socket so
``prompt_reply`` and ``close`` work while connecting: binary frames are input (dropped before
``ready``), ``resize`` is clamped to 1..1000, ``ping`` -> ``pong``, ``close`` kills the session,
and a dropped socket only detaches. Unknown or out-of-order messages -> ``bad_message`` (4400).

- While connecting, ``connect`` runs in its own task beside the receive loop: replies go to
  ``WsPrompter.deliver``; ``close`` cancels the pending prompt and the attempt (``error``
  ``cancelled``, close 1000); a dropped socket cancels the attempt, which aborts every hop.
  A ``resize`` while connecting is applied once the shell is up.
- After ``ready`` the session's pump sends output and the final ``exit`` or ``detached`` through
  this socket's ``_Sink``; this loop only reads. A socket that has been taken over ignores what
  it still receives, so it can never write to or kill the session it lost.
- Input frames over ``ws_max_frame_bytes`` close the socket with 1009 (uvicorn's ``ws_max_size``
  normally does it first). Input and output bytes are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect, WebSocketState

from portkey.api.schemas.connections import Connection
from portkey.api.schemas.ws import (
    AttachMsg,
    ClientMessage,
    CloseMsg,
    DetachedMsg,
    ExitMsg,
    OpenMsg,
    PingMsg,
    PongMsg,
    PromptReplyMsg,
    ReadyMsg,
    ResizeMsg,
)
from portkey.api.ws_common import WsPrompter, authenticate, parse_message, send, send_error
from portkey.errors import NotFound
from portkey.services import Services
from portkey.sessions.registry import MemorySessionRegistry
from portkey.sessions.terminal import ExitReason, TerminalSession
from portkey.ssh.errors import BadMessage, CloseCode, PromptCancelled, UnknownConnection, WsError

log = logging.getLogger("portkey.ws")

router = APIRouter()

Incoming = ClientMessage | bytes | None  # None: the client went away


@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    if not await authenticate(ws, services):
        return
    await _TerminalSocket(ws, services).run()


class _Socket:
    """Sends on one WebSocket, one message at a time. Sends raise once the socket is gone."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.lock = asyncio.Lock()

    async def json(self, msg: BaseModel) -> None:
        async with self.lock:
            await send(self.ws, msg)

    async def binary(self, data: bytes) -> None:
        async with self.lock:
            await self.ws.send_bytes(data)

    async def close(self, code: int) -> None:
        async with self.lock:
            if self.ws.application_state is WebSocketState.CONNECTED:
                with contextlib.suppress(Exception):
                    await self.ws.close(code=code)

    async def error(self, exc: BaseException) -> None:
        async with self.lock:
            await send_error(self.ws, exc)


class _Sink:
    """``sessions.terminal.SessionSink`` over this socket."""

    def __init__(
        self, sock: _Socket, session_id: str, connection: Connection, reattached: bool
    ) -> None:
        self.sock = sock
        self.session_id = session_id
        self.connection = connection
        self.reattached = reattached

    async def ready(self, offset: int, reset: bool) -> None:
        await self.sock.json(
            ReadyMsg(
                session_id=self.session_id,
                connection=self.connection,
                offset=offset,
                reset=reset,
                reattached=self.reattached,
            )
        )

    async def output(self, data: bytes) -> None:
        await self.sock.binary(data)

    async def exited(self, exit_status: int | None, signal: str | None, reason: ExitReason) -> None:
        await self.sock.json(ExitMsg(exit_status=exit_status, signal=signal, reason=reason))
        await self.sock.close(CloseCode.NORMAL)

    async def detached(self) -> None:
        await self.sock.json(DetachedMsg())
        await self.sock.close(CloseCode.NORMAL)

    async def lagged(self) -> None:
        await self.sock.close(CloseCode.INTERNAL)


class _TerminalSocket:
    def __init__(self, ws: WebSocket, services: Services) -> None:
        self.ws = ws
        self.services = services
        self.limits = services.config.limits
        self.registry = services.sessions
        self.sock = _Socket(ws)
        self.session: TerminalSession | None = None
        self.sink: _Sink | None = None
        self._getter: asyncio.Future[Incoming] | None = None

    async def run(self) -> None:
        try:
            first = await self._first()
            if first is None:
                return
            ok = await (self._open(first) if isinstance(first, OpenMsg) else self._attach(first))
            if ok and self.session is not None and self.sink is not None:
                await self._serve(self.session, self.sink)
        except WebSocketDisconnect:
            pass  # a send found the client gone: same as a drop
        except Exception as exc:  # WsError -> its code; anything else -> internal, 1011
            await self.sock.error(exc)
        finally:
            if self._getter is not None:
                self._getter.cancel()
            if self.session is not None and self.sink is not None:
                self.session.detach(self.sink)

    # --- receiving -----------------------------------------------------------------------------

    async def _next(self) -> Incoming:
        message = await self.ws.receive()
        if message["type"] == "websocket.disconnect":
            return None
        data = message.get("bytes")
        if data is not None:
            return bytes(data)
        text = message.get("text")
        if text is None:
            raise BadMessage()
        return parse_message(text)

    def _take_getter(self) -> asyncio.Future[Incoming]:
        getter = self._getter or asyncio.ensure_future(self._next())
        self._getter = None
        return getter

    async def _recv(self) -> Incoming:
        return await self._take_getter()

    def _too_big(self, data: bytes) -> bool:
        return len(data) > self.limits.ws_max_frame_bytes

    async def _first(self) -> OpenMsg | AttachMsg | None:
        """The second message must be ``open`` or ``attach``; input before it is dropped."""
        while True:
            msg = await self._recv()
            if msg is None:
                return None
            if isinstance(msg, bytes):
                if self._too_big(msg):
                    await self.sock.close(CloseCode.TOO_BIG)
                    return None
                continue
            if isinstance(msg, OpenMsg | AttachMsg):
                return msg
            raise BadMessage()

    # --- open ----------------------------------------------------------------------------------

    async def _open(self, msg: OpenMsg) -> bool:
        prompter = WsPrompter(self.ws, self.limits)
        size = (msg.cols, msg.rows)
        task = asyncio.ensure_future(
            self.registry.create(
                msg.connection_id, msg.cols, msg.rows, prompter, msg.force_password
            )
        )
        try:
            while not task.done():
                getter = self._take_getter()
                both: set[asyncio.Future[Any]] = {task, getter}
                done, _ = await asyncio.wait(both, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    self._getter = getter  # the serve loop picks it up
                    break
                incoming = getter.result()  # BadMessage propagates (the attempt is cancelled)
                if incoming is None:
                    await _cancel(task)
                    return False
                if isinstance(incoming, bytes):
                    if self._too_big(incoming):
                        await _cancel(task)
                        await self.sock.close(CloseCode.TOO_BIG)
                        return False
                elif isinstance(incoming, PromptReplyMsg):
                    prompter.deliver(incoming)
                elif isinstance(incoming, PingMsg):
                    await self.sock.json(PongMsg())
                elif isinstance(incoming, ResizeMsg):
                    size = (incoming.cols, incoming.rows)
                elif isinstance(incoming, CloseMsg):
                    prompter.cancel_pending()
                    session = await _cancel(task)
                    if session is not None:
                        await _kill_quietly(self.registry, session)
                    await self.sock.error(PromptCancelled())
                    return False
                else:
                    raise BadMessage()
        except BaseException:
            await _cancel(task)
            raise
        session = task.result()  # SshError, PromptTimeout, ... -> run() reports it
        if size != (msg.cols, msg.rows):
            await session.resize(*size)
        return await self._start(session, None, reattached=False)

    # --- attach --------------------------------------------------------------------------------

    async def _attach(self, msg: AttachMsg) -> bool:
        session = self.registry.get(msg.session_id)  # SessionGone -> 4410
        if msg.cols is not None or msg.rows is not None:
            await session.resize(msg.cols or session.cols, msg.rows or session.rows)
        return await self._start(session, msg.since, reattached=True)

    async def _start(
        self, session: TerminalSession, since: int | None, *, reattached: bool
    ) -> bool:
        record = await self.registry.connection_of(session)
        if record is None:
            raise UnknownConnection()
        connection = Connection.model_validate(record, from_attributes=True)
        self.session = session
        self.sink = _Sink(self.sock, session.session_id, connection, reattached)
        await session.attach(self.sink, since)  # the session's pump sends ready, then output
        return True

    # --- after ready ---------------------------------------------------------------------------

    async def _serve(self, session: TerminalSession, sink: _Sink) -> None:
        while True:
            msg = await self._recv()
            if msg is None:
                return
            current = session.has_sink(sink)
            if isinstance(msg, bytes):
                if self._too_big(msg):
                    await self.sock.close(CloseCode.TOO_BIG)
                    return
                if current:
                    await session.write(msg)
            elif isinstance(msg, ResizeMsg):
                if current:
                    await session.resize(msg.cols, msg.rows)
            elif isinstance(msg, PingMsg):
                await self.sock.json(PongMsg())
            elif isinstance(msg, CloseMsg):
                if current:
                    await _kill_quietly(self.registry, session)  # the pump sends exit, closes
                else:
                    await self.sock.close(CloseCode.NORMAL)
            elif isinstance(msg, PromptReplyMsg):
                continue  # a stale reply: ignored
            else:
                raise BadMessage()


async def _cancel(task: asyncio.Future[TerminalSession]) -> TerminalSession | None:
    """Cancel a connect attempt and wait for it; the session if it had already finished."""
    task.cancel()
    await asyncio.wait({task})
    if task.cancelled() or task.exception() is not None:
        return None
    return task.result()


async def _kill_quietly(registry: MemorySessionRegistry, session: TerminalSession) -> None:
    try:
        await registry.kill(session.session_id)
    except NotFound:
        await session.close("killed")  # already dropped (ended); make sure it is closed
    except WsError as exc:
        log.info("close of session %s failed: %s", session.session_id, exc.code)
