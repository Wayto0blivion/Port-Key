"""Host routes (package 3): known_hosts suggestions and app-trusted host keys.
Contract: docs/api.md "Hosts"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import ForgetHostKeyRequest, HostKeyList, HostPort, SuggestionList

router = APIRouter(tags=["hosts"], dependencies=[Depends(require_auth)])


@router.get("/api/hosts/suggestions", response_model=SuggestionList)
def list_suggestions(services: ServicesDep) -> SuggestionList:
    raise NotImplementedError("package 3")


@router.post("/api/hosts/suggestions/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_suggestion(body: HostPort, services: ServicesDep) -> None:
    raise NotImplementedError("package 3")


@router.post("/api/hosts/suggestions/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_suggestion(body: HostPort, services: ServicesDep) -> None:
    raise NotImplementedError("package 3")


@router.get("/api/hostkeys", response_model=HostKeyList)
def list_host_keys(services: ServicesDep) -> HostKeyList:
    raise NotImplementedError("package 3")


@router.delete("/api/hostkeys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_host_key(key_id: int, body: ForgetHostKeyRequest, services: ServicesDep) -> None:
    raise NotImplementedError("package 3")
