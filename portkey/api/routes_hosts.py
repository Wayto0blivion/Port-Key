"""Host routes (package 3): known_hosts suggestions and app-trusted host keys.
Contract: docs/api.md "Hosts".

Suggestions leave out hosts the owner dismissed and hosts a saved connection already uses
(by host or alias, at the same port), as docs/ux-spec.md asks; ``saved`` is therefore false on
every suggestion returned.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    ForgetHostKeyRequest,
    HostKeyList,
    HostKeyOut,
    HostPort,
    SuggestionList,
    SuggestionOut,
)
from portkey.errors import ErrorCode, Invalid

router = APIRouter(tags=["hosts"], dependencies=[Depends(require_auth)])


@router.get("/api/hosts/suggestions", response_model=SuggestionList)
def list_suggestions(services: ServicesDep) -> SuggestionList:
    scan = services.known_hosts.suggestions()
    hidden = set(services.dismissed.list_all())
    hidden.update((c.host.lower(), c.port) for c in services.connections.list_all())
    out: list[SuggestionOut] = []
    for s in scan.suggestions:
        names = (s.host, *s.aliases)
        if any((n.lower(), s.port) in hidden for n in names):
            continue
        out.append(
            SuggestionOut(
                host=s.host,
                port=s.port,
                aliases=list(s.aliases),
                key_types=list(s.key_types),
                saved=False,
            )
        )
    return SuggestionList(suggestions=out, hashed_count=scan.hashed_count)


@router.post("/api/hosts/suggestions/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_suggestion(body: HostPort, services: ServicesDep) -> None:
    services.dismissed.dismiss(body.host.lower(), body.port)


@router.post("/api/hosts/suggestions/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_suggestion(body: HostPort, services: ServicesDep) -> None:
    services.dismissed.restore(body.host.lower(), body.port)


@router.get("/api/hostkeys", response_model=HostKeyList)
def list_host_keys(services: ServicesDep) -> HostKeyList:
    return HostKeyList(
        host_keys=[
            HostKeyOut.model_validate(r, from_attributes=True)
            for r in services.host_keys.list_all()
        ]
    )


@router.delete("/api/hostkeys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_host_key(key_id: int, body: ForgetHostKeyRequest, services: ServicesDep) -> None:
    record = services.host_keys.get(key_id)
    if body.confirm_host.strip().lower() != record.host.lower():
        raise Invalid(
            f"The name you typed doesn't match {record.host}.",
            code=ErrorCode.CONFIRM_MISMATCH,
        )
    services.host_keys.delete(key_id)
    services.events.record(
        "hostkey_forgotten", host=record.host, port=record.port, fp=record.fp_sha256
    )
