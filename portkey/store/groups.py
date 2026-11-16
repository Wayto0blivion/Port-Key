"""Folders repository (package 2). Implements ``interfaces.GroupsRepo``.

Folders form a tree at most ``limits.max_group_depth`` (3) levels deep. Sibling names are unique
case-insensitively (enforced by the ``groups_sibling_name`` index; map the IntegrityError to
``Conflict(name_taken)``). Moving a folder under itself or a descendant is
``Invalid(group_cycle)``; a move that would push any folder in the moved subtree below level 3
is ``Invalid(group_too_deep)``. Deleting re-parents child folders and connections to the
deleted folder's parent (or root / ungrouped), all in one transaction, and never deletes
connections. ``connection_count`` includes descendants.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from portkey.config import Limits
from portkey.models import GroupRecord
from portkey.store.db import Database


class SqliteGroupsRepo:
    def __init__(self, db: Database, limits: Limits) -> None:
        self.db = db
        self.limits = limits

    def list_all(self) -> list[GroupRecord]:
        raise NotImplementedError("package 2")

    def get(self, group_id: str) -> GroupRecord:
        raise NotImplementedError("package 2")

    def create(self, name: str, parent_id: str | None) -> GroupRecord:
        raise NotImplementedError("package 2")

    def update(self, group_id: str, changes: Mapping[str, Any]) -> GroupRecord:
        raise NotImplementedError("package 2")

    def delete(self, group_id: str) -> None:
        raise NotImplementedError("package 2")

    def path(self, group_id: str) -> list[GroupRecord]:
        raise NotImplementedError("package 2")
