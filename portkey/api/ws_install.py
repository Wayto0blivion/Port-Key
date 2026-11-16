"""/ws/install (package 6). Contract: docs/ws-protocol.md "/ws/install".

Flow: ``ws_common.authenticate`` -> ``start`` -> ``services.wizard.run`` with a ``WsPrompter``
and a ``WizardIO`` that sends ``step``/``needs_action`` and waits for ``action``. Ends with
``done`` and close 1000, or ``error`` and its close code. The receive loop keeps running for
``prompt_reply``, ``action``, ``ping`` and ``close``. The run is abandoned after
``limits.wizard_idle_timeout`` without client activity. Closing the socket drops the password.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/install")
async def install_ws(ws: WebSocket) -> None:
    raise NotImplementedError("package 6")
