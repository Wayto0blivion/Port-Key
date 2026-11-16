"""Connection routes (package 2): /api/connections*. Contract: docs/api.md "Connections".

Use ``body.model_dump(exclude_unset=True)`` for PATCH so absent fields stay unchanged and an
explicit null clears. Reachability is a TCP connect to the SSH port with
``limits.reachability_timeout``; connections with a jump are ``skipped``; unknown ids are left
out of the results.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    BulkRequest,
    Connection,
    ConnectionCreate,
    ConnectionList,
    ConnectionUpdate,
    ReachabilityRequest,
    ReachabilityResponse,
)

router = APIRouter(
    prefix="/api/connections", tags=["connections"], dependencies=[Depends(require_auth)]
)


@router.get("", response_model=ConnectionList)
def list_connections(services: ServicesDep) -> ConnectionList:
    raise NotImplementedError("package 2")


@router.post("", response_model=Connection, status_code=status.HTTP_201_CREATED)
def create_connection(body: ConnectionCreate, services: ServicesDep) -> Connection:
    raise NotImplementedError("package 2")


@router.post("/bulk", response_model=ConnectionList)
def bulk_update(body: BulkRequest, services: ServicesDep) -> ConnectionList:
    raise NotImplementedError("package 2")


@router.post("/reachability", response_model=ReachabilityResponse)
async def reachability(body: ReachabilityRequest, services: ServicesDep) -> ReachabilityResponse:
    raise NotImplementedError("package 2")


@router.patch("/{conn_id}", response_model=Connection)
def update_connection(conn_id: str, body: ConnectionUpdate, services: ServicesDep) -> Connection:
    raise NotImplementedError("package 2")


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(conn_id: str, services: ServicesDep) -> None:
    raise NotImplementedError("package 2")


@router.post("/{conn_id}/duplicate", response_model=Connection, status_code=status.HTTP_201_CREATED)
def duplicate_connection(conn_id: str, services: ServicesDep) -> Connection:
    raise NotImplementedError("package 2")
