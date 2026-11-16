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
- ``send``/``send_error``: serialise server messages (``exclude_none``) and report an exception
  as ``error`` followed by the close code from ``ssh.errors.close_code_for``.
- ``hop_out``: ``models.HopRef`` -> ``schemas.ws.Hop``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel

from portkey.api.schemas.ws import Hop, PromptReplyMsg
from portkey.config import Limits
from portkey.models import HopRef, KeyRecord
from portkey.services import Services


def hop_out(hop: HopRef) -> Hop:
    return Hop.model_validate(hop, from_attributes=True)


async def send(ws: WebSocket, msg: BaseModel) -> None:
    raise NotImplementedError("package 4")


async def send_error(ws: WebSocket, exc: BaseException) -> None:
    """Send ``{"type":"error",...}`` for ``exc`` (generic text for unexpected exceptions) and
    close with the matching code. Never includes secrets or stack traces."""
    raise NotImplementedError("package 4")


async def authenticate(ws: WebSocket, services: Services) -> bool:
    raise NotImplementedError("package 4")


class WsPrompter:
    """``interfaces.Prompter`` over one WebSocket."""

    def __init__(self, ws: WebSocket, limits: Limits) -> None:
        self.ws = ws
        self.limits = limits

    def deliver(self, reply: PromptReplyMsg) -> None:
        """Called by the handler's receive loop for every ``prompt_reply``."""
        raise NotImplementedError("package 4")

    def cancel_pending(self) -> None:
        """Fail the outstanding prompt with ``PromptCancelled`` (client sent ``close``)."""
        raise NotImplementedError("package 4")

    async def status(self, phase: str, hop: HopRef, **detail: Any) -> None:
        raise NotImplementedError("package 4")

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool:
        raise NotImplementedError("package 4")

    async def hostkey_changed(
        self,
        hop: HopRef,
        key_type: str,
        old_fingerprint: str,
        new_fingerprint: str,
        first_seen_at: datetime | None,
    ) -> tuple[bool, str]:
        raise NotImplementedError("package 4")

    async def passphrase(self, hop: HopRef, key: KeyRecord, retry: bool) -> tuple[str, bool]:
        raise NotImplementedError("package 4")

    async def password(self, hop: HopRef, user: str, retry: bool) -> str:
        raise NotImplementedError("package 4")

    async def kbdint(
        self, hop: HopRef, name: str, instructions: str, prompts: list[tuple[str, bool]]
    ) -> list[str]:
        raise NotImplementedError("package 4")
