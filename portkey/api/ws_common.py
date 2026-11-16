"""WebSocket plumbing shared by /ws/terminal and /ws/install (package 4).

Host and Origin are already enforced by ``security.hostguard`` before the handler runs. This
module provides:

- ``authenticate(ws, services)``: accept, then require ``{"type":"auth","token"}`` as the first
  message within ``limits.ws_auth_timeout`` (5 s). On failure send ``error`` (``unauthorized`` or
  ``auth_timeout``) and close 4401; returns False. Tokens are never logged.
- ``WsPrompter``: implements ``interfaces.Prompter`` over the socket. Each ask sends a
  ``prompt`` with a fresh id and awaits the matching ``prompt_reply``, which the handler's receive
  loop feeds in through ``deliver``. ``{"cancel": true}`` -> ``PromptCancelled``; no reply within
  ``limits.prompt_timeout`` -> ``PromptTimeout``. Replies to unknown or stale ids are ignored.
  Only one prompt is outstanding at a time. Reply values are dropped right after use.
- ``send``/``send_error``: serialise server messages (optional fields that are None are left
  out, required nullable ones stay ``null``) and report an exception as ``error`` followed by
  the close code from ``ssh.errors.close_code_for``.
- ``parse_message``: validate a client text frame; any problem is ``BadMessage`` (4400) whose
  text never repeats the frame (a ``prompt_reply`` carries secrets).
- ``hop_out``: ``models.HopRef`` -> ``schemas.ws.Hop``.

Prompt replies carry passwords, passphrases and one-time codes: nothing here logs a reply,
puts one in an exception message or keeps one after the ask returns.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketState

from portkey.api.schemas.ws import (
    AuthMsg,
    ClientMessage,
    ErrorMsg,
    Hop,
    HostKeyChangedPrompt,
    HostKeyNewPrompt,
    KbdintPrompt,
    KbdintQuestion,
    PassphrasePrompt,
    PasswordPrompt,
    PromptReplyMsg,
    StatusMsg,
    parse_client_message,
)
from portkey.config import Limits
from portkey.models import HopRef, KeyRecord
from portkey.services import Services
from portkey.ssh.errors import (
    BadMessage,
    PromptCancelled,
    PromptTimeout,
    WsError,
    WsErrorCode,
    WsUnauthorized,
    close_code_for,
)

log = logging.getLogger("portkey.ws")

INTERNAL_MESSAGE = "Something went wrong inside Port-Key."
AUTH_TIMEOUT_MESSAGE = "Port-Key didn't receive a sign-in in time. Reload the page to try again."


def hop_out(hop: HopRef) -> Hop:
    return Hop.model_validate(hop, from_attributes=True)


# --- framing ------------------------------------------------------------------------------------


def dump(msg: BaseModel) -> dict[str, Any]:
    """``msg`` as JSON-ready data: optional fields that are None are dropped, required fields
    (nullable or not) are always present."""
    data = msg.model_dump(mode="json", by_alias=True)
    return _prune(msg, data)


def _prune(model: BaseModel, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        key = field.alias or name
        if key not in data:
            continue
        value, attr = data[key], getattr(model, name)
        if value is None and not field.is_required():
            continue
        if isinstance(attr, BaseModel):
            value = _prune(attr, value)
        elif isinstance(attr, list):
            value = [
                _prune(a, v) if isinstance(a, BaseModel) else v
                for a, v in zip(attr, value, strict=True)
            ]
        out[key] = value
    return out


async def send(ws: WebSocket, msg: BaseModel) -> None:
    await ws.send_json(dump(msg))


def parse_message(text: str | bytes) -> ClientMessage:
    """Validate one client text frame. Raises ``BadMessage`` without echoing anything."""
    try:
        return parse_client_message(text)
    except ValidationError:
        raise BadMessage() from None


def error_message(exc: BaseException) -> ErrorMsg:
    """The ``error`` message for ``exc``: generic text for anything that is not a ``WsError``."""
    close = close_code_for(exc)
    if not isinstance(exc, WsError):
        return ErrorMsg(code=WsErrorCode.INTERNAL, message=INTERNAL_MESSAGE, close=close)
    methods = getattr(exc, "methods", None) or exc.extra.get("methods")
    cause = exc.extra.get("cause")
    return ErrorMsg(
        code=exc.code,
        message=exc.message,
        close=close,
        hop=hop_out(exc.hop) if exc.hop is not None else None,
        methods=list(methods) if methods else None,
        cause=str(cause) if cause else None,
    )


async def send_error(ws: WebSocket, exc: BaseException) -> None:
    """Send ``{"type":"error",...}`` for ``exc`` (generic text for unexpected exceptions) and
    close with the matching code. Never includes secrets or stack traces. Safe to call when the
    socket is already gone."""
    msg = error_message(exc)
    if not isinstance(exc, WsError):
        # Type only: an unexpected exception's text may quote anything, a reply included.
        log.error("websocket ended by an unexpected %s", type(exc).__name__)
    if ws.application_state is not WebSocketState.CONNECTED:
        return
    try:
        await send(ws, msg)
        await ws.close(code=msg.close)
    except Exception:  # noqa: S110 - the peer is gone; there is nobody left to tell
        pass


# --- the auth handshake ---------------------------------------------------------------------------


async def authenticate(ws: WebSocket, services: Services) -> bool:
    """Accept the socket and require a valid ``auth`` message first, within the time limit."""
    await ws.accept()
    limits = services.config.limits
    try:
        message = await asyncio.wait_for(ws.receive(), timeout=limits.ws_auth_timeout)
    except TimeoutError:
        await send_error(ws, WsUnauthorized(AUTH_TIMEOUT_MESSAGE, code=WsErrorCode.AUTH_TIMEOUT))
        return False
    if message.get("type") == "websocket.disconnect":
        return False
    token = _auth_token(message.get("text"))
    if token is None or not await asyncio.to_thread(services.tokens.verify, token):
        await send_error(ws, WsUnauthorized())
        return False
    return True


def _auth_token(text: object) -> str | None:
    if not isinstance(text, str):
        return None  # binary frame or nothing: not an auth message
    try:
        msg = parse_client_message(text)
    except ValidationError:
        return None
    return msg.token if isinstance(msg, AuthMsg) else None


# --- prompts ------------------------------------------------------------------------------------


class WsPrompter:
    """``interfaces.Prompter`` over one WebSocket."""

    def __init__(self, ws: WebSocket, limits: Limits) -> None:
        self.ws = ws
        self.limits = limits
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self._pending: tuple[str, asyncio.Future[PromptReplyMsg]] | None = None
        self._cancelled = False

    @property
    def pending_id(self) -> str | None:
        """Id of the prompt waiting for a reply, if any."""
        return self._pending[0] if self._pending else None

    def deliver(self, reply: PromptReplyMsg) -> None:
        """Called by the handler's receive loop for every ``prompt_reply``."""
        pending = self._pending
        if pending is None or pending[0] != reply.id or pending[1].done():
            return  # unknown or stale id: ignored
        pending[1].set_result(reply)

    def cancel_pending(self) -> None:
        """Fail the outstanding prompt with ``PromptCancelled`` (client sent ``close``). Any
        later prompt fails the same way at once."""
        self._cancelled = True
        if self._pending is not None and not self._pending[1].done():
            self._pending[1].set_exception(PromptCancelled())

    async def status(self, phase: str, hop: HopRef, **detail: Any) -> None:
        await send(
            self.ws,
            StatusMsg.model_validate(
                {
                    "phase": phase,
                    "hop": hop_out(hop),
                    "method": detail.get("method"),
                    "key_name": detail.get("key_name"),
                }
            ),
        )

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool:
        reply = await self._ask(
            lambda pid: HostKeyNewPrompt(
                id=pid,
                hop=hop_out(hop),
                host=hop.host,
                port=hop.port,
                key_type=key_type,
                fingerprint=fingerprint,
            )
        )
        return _require(reply.accept)

    async def hostkey_changed(
        self,
        hop: HopRef,
        key_type: str,
        old_fingerprint: str,
        new_fingerprint: str,
        first_seen_at: datetime | None,
        *,
        old_key_type: str | None = None,
    ) -> tuple[bool, str]:
        reply = await self._ask(
            lambda pid: HostKeyChangedPrompt(
                id=pid,
                hop=hop_out(hop),
                host=hop.host,
                port=hop.port,
                key_type=key_type,
                old_key_type=old_key_type or key_type,
                old_fingerprint=old_fingerprint,
                new_fingerprint=new_fingerprint,
                first_seen_at=first_seen_at,
                confirm_name=hop.name,
            )
        )
        accept = _require(reply.accept)
        return accept, (reply.confirm_name or "") if accept else ""

    async def passphrase(self, hop: HopRef, key: KeyRecord, retry: bool) -> tuple[str, bool]:
        reply = await self._ask(
            lambda pid: PassphrasePrompt(
                id=pid,
                hop=hop_out(hop),
                key_fp=key.fp,
                key_name=key.name,
                path=key.display_path,
                retry=retry,
            )
        )
        value = _require(reply.value)
        return value.get_secret_value(), bool(reply.remember)

    async def password(self, hop: HopRef, user: str, retry: bool) -> str:
        reply = await self._ask(
            lambda pid: PasswordPrompt(
                id=pid, hop=hop_out(hop), user=user, host=hop.host, port=hop.port, retry=retry
            )
        )
        return _require(reply.value).get_secret_value()

    async def kbdint(
        self,
        hop: HopRef,
        name: str,
        instructions: str,
        prompts: list[tuple[str, bool]],
        *,
        retry: bool = False,
    ) -> list[str]:
        reply = await self._ask(
            lambda pid: KbdintPrompt(
                id=pid,
                hop=hop_out(hop),
                name=name,
                instructions=instructions,
                prompts=[KbdintQuestion(text=t, echo=e) for t, e in prompts],
                retry=retry,
            )
        )
        values = reply.values if reply.values is not None else ([] if not prompts else None)
        if values is None or len(values) != len(prompts):
            raise BadMessage()
        return [v.get_secret_value() for v in values]

    async def _ask(self, build: Callable[[str], BaseModel]) -> PromptReplyMsg:
        """Send one prompt and wait for its reply (cancel and timeout raise)."""
        async with self._lock:
            if self._cancelled:
                raise PromptCancelled()
            prompt_id = f"p{next(self._ids)}"
            future: asyncio.Future[PromptReplyMsg] = asyncio.get_running_loop().create_future()
            self._pending = (prompt_id, future)
            try:
                await send(self.ws, build(prompt_id))
                try:
                    reply = await asyncio.wait_for(future, timeout=self.limits.prompt_timeout)
                except TimeoutError:
                    raise PromptTimeout() from None
            finally:
                self._pending = None
            if reply.cancel:
                raise PromptCancelled()
            return reply


def _require[T](value: T | None) -> T:
    """A reply field the prompt's kind needs; missing -> ``BadMessage`` (close 4400)."""
    if value is None:
        raise BadMessage()
    return value
