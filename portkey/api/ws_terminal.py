"""/ws/terminal (package 5). Contract: docs/ws-protocol.md "/ws/terminal".

Flow: ``ws_common.authenticate`` -> first message ``open`` (create a session through
``services.sessions.create`` with a ``WsPrompter``) or ``attach`` (``SessionGone`` -> 4410) ->
``ready`` -> binary output frames. The receive loop runs for the whole life of the socket so
``prompt_reply`` and ``close`` work while connecting: binary frames are input (dropped before
``ready``), ``resize`` is clamped to 1..1000, ``ping`` -> ``pong``, ``close`` kills the session,
and a dropped socket only detaches. Unknown or out-of-order messages -> ``bad_message`` (4400).
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket) -> None:
    raise NotImplementedError("package 5")
