"""Key routes (package 3): /api/keys*. Contract: docs/api.md "Keys".

``{key_id}`` is ``schemas.fp_to_id(fp)``; convert with ``fp_from_id`` (None -> 404
``key_unknown``). Key generation and unlocking are CPU-bound: run them with
``anyio.to_thread.run_sync`` (or keep the route sync).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import PlainTextResponse

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    GenerateKeyRequest,
    KeyInfo,
    KeyListResponse,
    KeyNameCheck,
    KeyUpdate,
    UnlockRequest,
)

router = APIRouter(prefix="/api/keys", tags=["keys"], dependencies=[Depends(require_auth)])


@router.get("", response_model=KeyListResponse)
def list_keys(services: ServicesDep) -> KeyListResponse:
    raise NotImplementedError("package 3")


@router.get("/name-check", response_model=KeyNameCheck)
def check_key_name(services: ServicesDep, name: str = Query(max_length=128)) -> KeyNameCheck:
    raise NotImplementedError("package 3")


@router.post("/generate", response_model=KeyInfo, status_code=status.HTTP_201_CREATED)
def generate_key(body: GenerateKeyRequest, services: ServicesDep) -> KeyInfo:
    raise NotImplementedError("package 3")


@router.post("/lock", status_code=status.HTTP_204_NO_CONTENT)
def lock_keys(services: ServicesDep) -> None:
    raise NotImplementedError("package 3")


@router.get("/{key_id}/public", response_class=PlainTextResponse)
def public_key(key_id: str, services: ServicesDep) -> PlainTextResponse:
    raise NotImplementedError("package 3")


@router.patch("/{key_id}", response_model=KeyInfo)
def update_key(key_id: str, body: KeyUpdate, services: ServicesDep) -> KeyInfo:
    raise NotImplementedError("package 3")


@router.post("/{key_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
def unlock_key(key_id: str, body: UnlockRequest, services: ServicesDep) -> None:
    raise NotImplementedError("package 3")
