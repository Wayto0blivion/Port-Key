"""Session routes (package 5): /api/sessions*. Contract: docs/api.md "Sessions".

Both routes are ``async``: the registry lives on the event loop and is not thread-safe, so they
must not run in the thread pool. ``DELETE`` kills the shell and every hop; an attached socket
gets ``exit`` with reason ``killed``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import SessionList, SessionOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=[Depends(require_auth)])


@router.get("", response_model=SessionList)
async def list_sessions(services: ServicesDep) -> SessionList:
    return SessionList(
        sessions=[
            SessionOut.model_validate(s, from_attributes=True) for s in services.sessions.list_all()
        ]
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def kill_session(session_id: str, services: ServicesDep) -> None:
    await services.sessions.kill(session_id)  # NotFound(session_gone) -> 404
