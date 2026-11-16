"""Key routes (package 3): /api/keys*. Contract: docs/api.md "Keys".

``{key_id}`` is ``schemas.fp_to_id(fp)``; convert with ``fp_from_id`` (None -> 404
``key_unknown``). Key generation and unlocking are CPU-bound, so these routes are sync and run
in FastAPI's thread pool. Passphrases are read from ``SecretStr`` only where they are used and
never logged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import PlainTextResponse

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    ConnectionRefOut,
    GenerateKeyRequest,
    KeyInfo,
    KeyListResponse,
    KeyNameCheck,
    KeyProblemOut,
    KeyUpdate,
    UnlockRequest,
    fp_from_id,
    fp_to_id,
)
from portkey.errors import Conflict, ErrorCode, Invalid
from portkey.keys.discovery import key_unknown, load_private_from_disk, pick_default
from portkey.models import ConnectionRef, KeyGenSpec, KeyRecord
from portkey.services import Services

router = APIRouter(prefix="/api/keys", tags=["keys"], dependencies=[Depends(require_auth)])


def _fp(key_id: str) -> str:
    fp = fp_from_id(key_id)
    if fp is None:
        raise key_unknown()
    return fp


def _find(records: list[KeyRecord], fp: str) -> KeyRecord:
    for record in records:
        if record.fp == fp:
            return record
    raise key_unknown()


def _default_fp(services: Services, records: list[KeyRecord]) -> str | None:
    default = pick_default(records, services.settings.get().default_key_fp)
    return default.fp if default else None


def _info(
    services: Services,
    record: KeyRecord,
    default_fp: str | None,
    unlocked: set[str],
) -> KeyInfo:
    used_by = services.connections.using_key(record.fp, default_fp)
    return KeyInfo(
        id=fp_to_id(record.fp),
        fp=record.fp,
        name=record.name,
        label=record.label,
        type=record.type,
        bits=record.bits,
        comment=record.comment,
        path=record.display_path,
        source=record.source,
        encrypted=record.encrypted,
        unlocked=record.fp in unlocked,
        public_key=record.public_key,
        is_default=record.fp == default_fp,
        hidden=record.hidden,
        used_by=[ConnectionRefOut(id=c.id, name=c.name) for c in used_by],
    )


def _describe(services: Services, fp: str) -> KeyInfo:
    records = services.keys.list_keys()
    record = _find(records, fp)
    return _info(services, record, _default_fp(services, records), set(services.vault.unlocked()))


@router.get("", response_model=KeyListResponse)
def list_keys(services: ServicesDep) -> KeyListResponse:
    records = services.keys.list_keys()
    default_fp = _default_fp(services, records)
    unlocked = set(services.vault.unlocked())
    return KeyListResponse(
        keys=[_info(services, r, default_fp, unlocked) for r in records],
        problems=[
            KeyProblemOut(path=p.display_path, reason=p.reason, detail=p.detail)
            for p in services.keys.problems()
        ],
        default_fp=default_fp,
        unlocked_count=len(unlocked),
    )


@router.get("/name-check", response_model=KeyNameCheck)
def check_key_name(services: ServicesDep, name: str = Query(max_length=128)) -> KeyNameCheck:
    available, reason = services.keys.name_available(name)
    return KeyNameCheck.model_validate({"name": name, "available": available, "reason": reason})


@router.post("/generate", response_model=KeyInfo, status_code=status.HTTP_201_CREATED)
def generate_key(body: GenerateKeyRequest, services: ServicesDep) -> KeyInfo:
    spec = KeyGenSpec(
        name=body.name,
        type=body.type,
        bits=body.bits,
        comment=body.comment,
        passphrase=body.passphrase.get_secret_value() if body.passphrase else None,
    )
    record = services.keys.generate(spec)
    services.events.record("key_generated", fp=record.fp, key_type=record.type)
    return _describe(services, record.fp)


@router.post("/lock", status_code=status.HTTP_204_NO_CONTENT)
def lock_keys(services: ServicesDep) -> None:
    services.vault.lock_all()


@router.get("/{key_id}/public", response_class=PlainTextResponse)
def public_key(key_id: str, services: ServicesDep) -> PlainTextResponse:
    record = services.keys.get(_fp(key_id))
    return PlainTextResponse(record.public_key + "\n")


@router.patch("/{key_id}", response_model=KeyInfo)
def update_key(key_id: str, body: KeyUpdate, services: ServicesDep) -> KeyInfo:
    fp = _fp(key_id)
    records = services.keys.list_keys()
    record = _find(records, fp)
    fields = body.model_fields_set
    if "label" in fields:
        services.keys_meta.set_label(fp, (body.label or "").strip() or None)
    if body.hidden is True and not record.hidden:
        _hide(services, record, records, body)
    elif body.hidden is False and record.hidden:
        services.keys_meta.set_hidden(fp, False)
    return _describe(services, fp)


def _hide(services: Services, record: KeyRecord, records: list[KeyRecord], body: KeyUpdate) -> None:
    """Hide ``record``; connections using it move to ``body.reassign`` first."""
    settings = services.settings.get()
    default_fp = _default_fp(services, records)
    users = services.connections.using_key(record.fp, default_fp)
    if users:
        if body.reassign is None:
            raise _in_use(record, users)
        target = body.reassign.key_fp
        if body.reassign.auth_method == "key" and not any(
            r.fp == target and r.fp != record.fp and not r.hidden for r in records
        ):
            raise Invalid("Choose a key that is listed and not hidden.", code=ErrorCode.KEY_UNKNOWN)
        services.connections.reassign_key(record.fp, default_fp, body.reassign.auth_method, target)
    if settings.default_key_fp == record.fp:
        services.settings.update({"default_key_fp": None})
    services.keys_meta.set_hidden(record.fp, True)


def _in_use(record: KeyRecord, users: list[ConnectionRef]) -> Conflict:
    count = len(users)
    names = ", ".join(u.name for u in users)
    noun = "connection" if count == 1 else "connections"
    return Conflict(
        f"{record.name} is used by {count} {noun}: {names}. Choose what they use instead.",
        code=ErrorCode.KEY_IN_USE,
        dependants=[{"id": u.id, "name": u.name} for u in users],
    )


@router.post("/{key_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
def unlock_key(key_id: str, body: UnlockRequest, services: ServicesDep) -> None:
    fp = _fp(key_id)
    record = services.keys.get(fp)
    if not record.encrypted:
        raise Conflict(
            f"{record.name} has no passphrase, so there is nothing to unlock.",
            code=ErrorCode.KEY_NOT_ENCRYPTED,
        )
    key = load_private_from_disk(record, body.passphrase.get_secret_value())
    ttl = (
        services.settings.get().passphrase_ttl
        if body.remember
        else services.config.limits.unlock_no_remember_ttl
    )
    services.vault.put(fp, key, ttl)
