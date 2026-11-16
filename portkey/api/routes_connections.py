"""Connection routes (package 2): /api/connections*. Contract: docs/api.md "Connections".

Use ``body.model_dump(exclude_unset=True)`` for PATCH so absent fields stay unchanged and an
explicit null clears. Reachability is a TCP connect to the SSH port with
``limits.reachability_timeout``; connections with a jump are ``skipped``; unknown ids are left
out of the results.

Repository calls are synchronous, so the CRUD routes are plain ``def`` and run in the thread
pool. Reachability is ``async``: it reads the records once in a worker thread, then runs the
TCP checks on the event loop with at most ``REACH_CONCURRENCY`` connects in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

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
    ReachabilityResult,
)
from portkey.errors import ErrorCode, Invalid, NotFound
from portkey.models import BulkChange, ConnectionDraft, ConnectionRecord
from portkey.services import Services

REACH_CONCURRENCY = 32
CLOSE_WAIT_S = 0.5

router = APIRouter(
    prefix="/api/connections", tags=["connections"], dependencies=[Depends(require_auth)]
)


def require_known_key(services: Services, fp: str | None) -> None:
    """422 ``key_unknown`` unless ``fp`` is None or names a key Port-Key can see and that is
    not hidden."""
    if fp is None:
        return
    try:
        key = services.keys.get(fp)
    except NotFound:
        key = None
    if key is None or key.hidden:
        raise Invalid(
            "Port-Key can't find that key. Choose another one on the Keys tab.",
            code=ErrorCode.KEY_UNKNOWN,
        )


def to_out(record: ConnectionRecord) -> Connection:
    return Connection.model_validate(record, from_attributes=True)


@router.get("", response_model=ConnectionList)
def list_connections(services: ServicesDep) -> ConnectionList:
    return ConnectionList(connections=[to_out(r) for r in services.connections.list_all()])


@router.post("", response_model=Connection, status_code=status.HTTP_201_CREATED)
def create_connection(body: ConnectionCreate, services: ServicesDep) -> Connection:
    require_known_key(services, body.key_fp)
    fields = body.model_dump()
    fields["tags"] = tuple(fields["tags"])
    return to_out(services.connections.create(ConnectionDraft(**fields)))


@router.post("/bulk", response_model=ConnectionList)
def bulk_update(body: BulkRequest, services: ServicesDep) -> ConnectionList:
    change = BulkChange(
        ids=tuple(body.ids),
        set_group="set_group_id" in body.model_fields_set,
        group_id=body.set_group_id,
        add_tags=tuple(body.add_tags),
        remove_tags=tuple(body.remove_tags),
    )
    return ConnectionList(connections=[to_out(r) for r in services.connections.bulk_update(change)])


async def _probe(host: str, port: int, limit_s: float) -> int | None:
    """Milliseconds to open a TCP connection (name lookup included), or None when it fails or
    takes longer than ``limit_s``. Nothing is sent; the socket is closed straight away."""
    start = time.monotonic()
    try:
        async with asyncio.timeout(limit_s):
            _, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError, UnicodeError):
        return None
    ms = round((time.monotonic() - start) * 1000)
    writer.close()
    with contextlib.suppress(OSError, TimeoutError):
        async with asyncio.timeout(CLOSE_WAIT_S):
            await writer.wait_closed()
    return ms


@router.post("/reachability", response_model=ReachabilityResponse, response_model_exclude_none=True)
async def reachability(body: ReachabilityRequest, services: ServicesDep) -> ReachabilityResponse:
    timeout = services.config.limits.reachability_timeout
    # One read, off the event loop, so a long write elsewhere never stalls terminals.
    by_id = {r.id: r for r in await asyncio.to_thread(services.connections.list_all)}
    records = [by_id[i] for i in dict.fromkeys(body.ids) if i in by_id]
    gate = asyncio.Semaphore(REACH_CONCURRENCY)

    async def check(record: ConnectionRecord) -> ReachabilityResult:
        if record.jump_id is not None:
            return ReachabilityResult(id=record.id, state="skipped")
        async with gate:
            ms = await _probe(record.host, record.port, timeout)
        if ms is None:
            return ReachabilityResult(id=record.id, state="down")
        return ReachabilityResult(id=record.id, state="up", ms=ms)

    results = await asyncio.gather(*(check(r) for r in records))
    return ReachabilityResponse(results=list(results))


@router.patch("/{conn_id}", response_model=Connection)
def update_connection(conn_id: str, body: ConnectionUpdate, services: ServicesDep) -> Connection:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("key_fp") is not None:
        require_known_key(services, changes["key_fp"])
    return to_out(services.connections.update(conn_id, changes))


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(conn_id: str, services: ServicesDep) -> None:
    services.connections.delete(conn_id)


@router.post("/{conn_id}/duplicate", response_model=Connection, status_code=status.HTTP_201_CREATED)
def duplicate_connection(conn_id: str, services: ServicesDep) -> Connection:
    return to_out(services.connections.duplicate(conn_id))


@router.post("/{conn_id}/restore", response_model=Connection, status_code=status.HTTP_201_CREATED)
def restore_connection(conn_id: str, services: ServicesDep) -> Connection:
    """Undo a delete from the last few minutes: the same id, tags, usage and dates."""
    return to_out(services.connections.restore(conn_id))
