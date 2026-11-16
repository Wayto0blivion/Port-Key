"""Session routes (package 5): /api/sessions*. Contract: docs/api.md "Sessions"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import SessionList

router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=[Depends(require_auth)])


@router.get("", response_model=SessionList)
def list_sessions(services: ServicesDep) -> SessionList:
    raise NotImplementedError("package 5")


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def kill_session(session_id: str, services: ServicesDep) -> None:
    raise NotImplementedError("package 5")
