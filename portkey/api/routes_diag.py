"""Diagnostics routes (package 7): /api/diag*. Contract: docs/api.md "Diagnostics".

``GET /api/diag`` reports mounts, writability, key problems and versions (never key material).
``POST /api/diag/resolve`` resolves a name from inside the container (``getaddrinfo``) and tries
a TCP connect to the port with ``limits.reachability_timeout``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import DiagReport, ResolveRequest, ResolveResult

router = APIRouter(prefix="/api/diag", tags=["diag"], dependencies=[Depends(require_auth)])


@router.get("", response_model=DiagReport)
def diagnostics(services: ServicesDep) -> DiagReport:
    raise NotImplementedError("package 7")


@router.post("/resolve", response_model=ResolveResult)
async def resolve(body: ResolveRequest, services: ServicesDep) -> ResolveResult:
    raise NotImplementedError("package 7")
