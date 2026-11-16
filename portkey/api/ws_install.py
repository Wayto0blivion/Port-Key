"""/ws/install (package 6). Contract: docs/ws-protocol.md "/ws/install".

Flow: ``ws_common.authenticate`` -> ``start`` -> ``services.wizard.run`` with a ``WsPrompter``
and a ``SocketIO`` (the ``WizardIO``) that sends ``step``/``needs_action`` and waits for
``action``. Ends with ``done`` and close 1000, or ``error`` and its close code. While the run
goes on, the receive loop keeps reading ``prompt_reply`` (to the prompter), ``action`` (to the
waiting ``needs_action``), ``ping`` (answered with ``pong``) and ``close`` (the run is
cancelled, every SSH connection it opened is closed, which drops the password, and the socket
closes with 1000). ``{"type":"action","action":"cancel"}`` is accepted at any time and ends
with ``error`` ``cancelled``. A binary frame, a second ``start`` or any other type is
``bad_message`` (4400). After ``limits.wizard_idle_timeout`` (10 minutes) without a message
from the client, the run is abandoned with ``prompt_timeout`` (4408). An action that arrives
when nothing is waiting for one is ignored, as a stale prompt reply is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import MutableMapping
from typing import Any, cast

from fastapi import APIRouter, WebSocket

from portkey.api.schemas.connections import Connection
from portkey.api.schemas.ws import (
    ActionMsg,
    CloseMsg,
    DoneMsg,
    NeedsActionMsg,
    PingMsg,
    PongMsg,
    PromptReplyMsg,
    StartMsg,
    StepMsg,
    WizardAction,
)
from portkey.api.ws_common import WsPrompter, authenticate, parse_message, send, send_error
from portkey.config import Limits
from portkey.models import ConnectionRecord
from portkey.services import Services
from portkey.ssh.errors import BadMessage, CloseCode, PromptCancelled, PromptTimeout, WsError

router = APIRouter()
log = logging.getLogger("portkey.ws")

IDLE_MESSAGE = (
    "Port-Key heard nothing from the browser for 10 minutes, so it stopped the wizard. "
    "Nothing was saved."
)


class _ClientGone(Exception):
    """The socket closed before ``start``."""


class _Closed:
    """The client sent ``close``."""


CLOSED = _Closed()
Ending = WsError | _Closed | None  # None: the client went away


class SocketIO:
    """``wizard.install.WizardIO`` over the socket."""

    def __init__(self, ws: WebSocket, limits: Limits) -> None:
        self.ws = ws
        self.limits = limits
        self._waiting: tuple[list[WizardAction], asyncio.Future[WizardAction]] | None = None

    @property
    def waiting(self) -> bool:
        return self._waiting is not None and not self._waiting[1].done()

    async def step(self, msg: StepMsg) -> None:
        await send(self.ws, msg)

    async def needs_action(self, msg: NeedsActionMsg) -> WizardAction:
        future: asyncio.Future[WizardAction] = asyncio.get_running_loop().create_future()
        self._waiting = (list(msg.actions), future)
        try:
            await send(self.ws, msg)
            try:
                return await asyncio.wait_for(future, timeout=self.limits.wizard_idle_timeout)
            except TimeoutError:
                raise PromptTimeout(IDLE_MESSAGE) from None
        finally:
            self._waiting = None

    def deliver(self, msg: ActionMsg) -> None:
        """Hand an ``action`` to the waiting ``needs_action``. One that was not offered is
        ``BadMessage``; ``cancel`` is always allowed."""
        waiting = self._waiting
        if waiting is None or waiting[1].done():
            return
        actions, future = waiting
        if msg.action != "cancel" and msg.action not in actions:
            raise BadMessage()
        future.set_result(msg.action)


@router.websocket("/ws/install")
async def install_ws(ws: WebSocket) -> None:
    services = cast(Services, ws.app.state.services)
    if not await authenticate(ws, services):
        return
    await InstallSocket(ws, services).serve()


class InstallSocket:
    def __init__(self, ws: WebSocket, services: Services) -> None:
        self.ws = ws
        self.services = services
        self.limits = services.config.limits
        self.prompter = WsPrompter(ws, self.limits)
        self.io = SocketIO(ws, self.limits)

    async def serve(self) -> None:
        try:
            start = await self._wait_start()
        except _ClientGone:
            return
        except WsError as exc:
            await send_error(self.ws, exc)
            return
        if start is None:
            await _close(self.ws)
            return
        run = asyncio.create_task(self.services.wizard.run(start, self.prompter, self.io))
        reader = asyncio.create_task(self._read())
        try:
            done, _ = await asyncio.wait({run, reader}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await _stop(run)
            await _stop(reader)
            raise
        if reader in done:
            await _stop(run)  # every SSH connection of the run is closed: the password is gone
            try:
                ending = reader.result()
            except Exception:  # the socket broke under the reader: nobody left to tell
                ending = None
            await self._end(ending)
            return
        await _stop(reader)
        await self._finish(run)

    async def _receive(self) -> MutableMapping[str, Any]:
        try:
            return await asyncio.wait_for(
                self.ws.receive(), timeout=self.limits.wizard_idle_timeout
            )
        except TimeoutError:
            raise PromptTimeout(IDLE_MESSAGE) from None

    async def _wait_start(self) -> StartMsg | None:
        """The second message: ``start`` (``ping`` is answered, ``close`` gives None)."""
        while True:
            message = await self._receive()
            if message.get("type") == "websocket.disconnect":
                raise _ClientGone()
            text = message.get("text")
            if text is None:
                raise BadMessage()
            msg = parse_message(text)
            if isinstance(msg, StartMsg):
                log.info("install wizard started for %s:%d", msg.host, msg.port)
                return msg
            if isinstance(msg, PingMsg):
                await send(self.ws, PongMsg())
            elif isinstance(msg, CloseMsg):
                return None
            else:
                raise BadMessage()

    async def _read(self) -> Ending:
        """Feed client messages to the run until the client closes, leaves or errs."""
        while True:
            try:
                message = await self._receive()
                if message.get("type") == "websocket.disconnect":
                    return None
                text = message.get("text")
                if text is None:
                    raise BadMessage()
                msg = parse_message(text)
                if isinstance(msg, PromptReplyMsg):
                    self.prompter.deliver(msg)
                elif isinstance(msg, ActionMsg):
                    if msg.action == "cancel" and not self.io.waiting:
                        return PromptCancelled()
                    self.io.deliver(msg)
                elif isinstance(msg, PingMsg):
                    await send(self.ws, PongMsg())
                elif isinstance(msg, CloseMsg):
                    return CLOSED
                else:
                    raise BadMessage()
            except WsError as exc:
                return exc

    async def _end(self, ending: Ending) -> None:
        if ending is None:
            log.info("install wizard: the browser left")
        elif isinstance(ending, _Closed):
            log.info("install wizard closed by the browser")
            await _close(self.ws)
        else:
            await send_error(self.ws, ending)

    async def _finish(self, run: asyncio.Task[tuple[ConnectionRecord, str]]) -> None:
        try:
            record, outcome = run.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await send_error(self.ws, exc)
            return
        done = DoneMsg.model_validate(
            {
                "connection": Connection.model_validate(record, from_attributes=True),
                "outcome": outcome,
            }
        )
        try:
            await send(self.ws, done)
        except Exception:
            return  # the browser left after the connection was saved
        await _close(self.ws)


async def _stop(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _close(ws: WebSocket) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=int(CloseCode.NORMAL))
