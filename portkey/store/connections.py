"""Connections repository (package 2). Implements ``interfaces.ConnectionsRepo``.

Rules to enforce (docs/architecture.md section 5):
- names unique case-insensitively (``Conflict(name_taken)``);
- ``jump_id`` must exist, no cycles, at most ``limits.max_jump_hops`` hops before the target;
- a connection used as a jump cannot be deleted (``Conflict(jump_in_use, dependants=[...])``);
- ``group_id`` must exist (``Invalid(group_unknown)``);
- tags are normalised with ``interfaces.normalize_tag``, validated against
  ``models.TAG_PATTERN`` (``Invalid(tag_invalid)``) and created on first use; the schema's
  trigger deletes a tag when its last use goes;
- ``bulk_update`` is one transaction: any unknown id or invalid value changes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from portkey.config import Limits
from portkey.models import BulkChange, ConnectionDraft, ConnectionRecord, ConnectionRef
from portkey.store.db import Database


class SqliteConnectionsRepo:
    def __init__(self, db: Database, limits: Limits) -> None:
        self.db = db
        self.limits = limits

    def list_all(self) -> list[ConnectionRecord]:
        raise NotImplementedError("package 2")

    def get(self, conn_id: str) -> ConnectionRecord:
        raise NotImplementedError("package 2")

    def find(self, conn_id: str) -> ConnectionRecord | None:
        raise NotImplementedError("package 2")

    def create(self, draft: ConnectionDraft) -> ConnectionRecord:
        raise NotImplementedError("package 2")

    def update(self, conn_id: str, changes: Mapping[str, Any]) -> ConnectionRecord:
        raise NotImplementedError("package 2")

    def delete(self, conn_id: str) -> None:
        raise NotImplementedError("package 2")

    def duplicate(self, conn_id: str) -> ConnectionRecord:
        raise NotImplementedError("package 2")

    def dependants(self, conn_id: str) -> list[ConnectionRef]:
        raise NotImplementedError("package 2")

    def resolve_chain(self, conn_id: str) -> list[ConnectionRecord]:
        raise NotImplementedError("package 2")

    def validate_jump(self, conn_id: str | None, jump_id: str | None) -> None:
        raise NotImplementedError("package 2")

    def touch_used(self, conn_id: str) -> None:
        raise NotImplementedError("package 2")

    def using_key(self, fp: str, default_fp: str | None) -> list[ConnectionRef]:
        raise NotImplementedError("package 2")

    def reassign_key(
        self, fp: str, default_fp: str | None, auth_method: str, key_fp: str | None
    ) -> list[ConnectionRef]:
        raise NotImplementedError("package 2")

    def bulk_update(self, change: BulkChange) -> list[ConnectionRecord]:
        raise NotImplementedError("package 2")
