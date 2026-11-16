"""Folders repository (package 2). Implements ``interfaces.GroupsRepo``.

Folders form a tree at most ``limits.max_group_depth`` (3) levels deep. Sibling names are unique
case-insensitively (enforced by the ``groups_sibling_name`` index; map the IntegrityError to
``Conflict(name_taken)``). Moving a folder under itself or a descendant is
``Invalid(group_cycle)``; a move that would push any folder in the moved subtree below level 3
is ``Invalid(group_too_deep)``. Deleting re-parents child folders and connections to the
deleted folder's parent (or root / ungrouped), all in one transaction, and never deletes
connections. ``connection_count`` includes descendants.

The tree is small (one person's folders), so each call loads the whole ``groups`` table inside
its transaction and works on it in memory.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from portkey.config import Limits
from portkey.errors import Conflict, ErrorCode, Invalid, NotFound
from portkey.models import GroupRecord
from portkey.store._common import clean_name, is_unique_violation, suffixed
from portkey.store.db import Database, from_iso, now_iso

_FIELDS = frozenset({"name", "parent_id", "sort_order"})


@dataclass(slots=True)
class _Node:
    id: str
    name: str
    parent_id: str | None
    sort_order: int
    created_at: str


class _Tree:
    """The folder table loaded in memory for one transaction."""

    def __init__(self, c: sqlite3.Connection) -> None:
        rows = c.execute("SELECT id, name, parent_id, sort_order, created_at FROM groups")
        self.nodes = {r[0]: _Node(r[0], r[1], r[2], int(r[3]), r[4]) for r in rows}
        self.children: dict[str | None, list[str]] = {}
        for node in self.nodes.values():
            self.children.setdefault(node.parent_id, []).append(node.id)
        self.direct = dict(
            c.execute(
                "SELECT group_id, count(*) FROM connections"
                " WHERE group_id IS NOT NULL GROUP BY group_id"
            ).fetchall()
        )

    def node(self, group_id: str) -> _Node:
        if group_id not in self.nodes:
            raise NotFound("That folder doesn't exist any more.")
        return self.nodes[group_id]

    def ancestors(self, group_id: str) -> list[str]:
        """``group_id`` and its ancestors, the folder itself first."""
        out: list[str] = []
        cur: str | None = group_id
        while cur is not None and cur not in out:
            out.append(cur)
            cur = self.nodes[cur].parent_id
        return out

    def level(self, group_id: str | None) -> int:
        """1 for a top-level folder; 0 for the root itself."""
        return 0 if group_id is None else len(self.ancestors(group_id))

    def subtree(self, group_id: str) -> list[str]:
        out = [group_id]
        i = 0
        while i < len(out):
            out.extend(k for k in self.children.get(out[i], ()) if k not in out)
            i += 1
        return out

    def height(self, group_id: str) -> int:
        """Levels in the subtree rooted at ``group_id`` (1 for a folder with no children)."""
        kids = self.children.get(group_id, [])
        return 1 + max((self.height(k) for k in kids), default=0)

    def count(self, group_id: str) -> int:
        return sum(int(self.direct.get(g, 0)) for g in self.subtree(group_id))

    def sibling_names(self, parent_id: str | None, exclude: str | None = None) -> set[str]:
        return {
            self.nodes[k].name.casefold() for k in self.children.get(parent_id, []) if k != exclude
        }

    def record(self, group_id: str) -> GroupRecord:
        n = self.node(group_id)
        return GroupRecord(
            id=n.id,
            name=n.name,
            parent_id=n.parent_id,
            sort_order=n.sort_order,
            created_at=from_iso(n.created_at),
            connection_count=self.count(n.id),
        )


def _name_taken(name: str) -> Conflict:
    return Conflict(
        f"A folder named {name} already exists here. Choose another name.",
        code=ErrorCode.NAME_TAKEN,
    )


def _parent_unknown() -> Invalid:
    return Invalid("That folder doesn't exist any more.", code=ErrorCode.GROUP_UNKNOWN)


class SqliteGroupsRepo:
    def __init__(self, db: Database, limits: Limits) -> None:
        self.db = db
        self.limits = limits

    def list_all(self) -> list[GroupRecord]:
        with self.db.tx() as c:
            tree = _Tree(c)
        records = [tree.record(g) for g in tree.nodes]
        records.sort(
            key=lambda r: (
                r.parent_id is not None,
                r.parent_id or "",
                r.sort_order,
                r.name.casefold(),
            )
        )
        return records

    def get(self, group_id: str) -> GroupRecord:
        with self.db.tx() as c:
            return _Tree(c).record(group_id)

    def path(self, group_id: str) -> list[GroupRecord]:
        with self.db.tx() as c:
            tree = _Tree(c)
            tree.node(group_id)
            return [tree.record(g) for g in reversed(tree.ancestors(group_id))]

    def create(self, name: str, parent_id: str | None) -> GroupRecord:
        clean = clean_name(name, "folder")
        with self.db.tx() as c:
            tree = _Tree(c)
            if parent_id is not None and parent_id not in tree.nodes:
                raise _parent_unknown()
            self._check_depth(tree.level(parent_id) + 1)
            if clean.casefold() in tree.sibling_names(parent_id):
                raise _name_taken(clean)
            siblings = [tree.nodes[k].sort_order for k in tree.children.get(parent_id, [])]
            group_id = str(uuid.uuid4())
            self._write(
                c,
                "INSERT INTO groups (id, name, parent_id, sort_order, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (group_id, clean, parent_id, max(siblings, default=-1) + 1, now_iso()),
                clean,
            )
            return _Tree(c).record(group_id)

    def update(self, group_id: str, changes: Mapping[str, Any]) -> GroupRecord:
        unknown = set(changes) - _FIELDS
        if unknown:
            raise Invalid("Unknown folder field: " + ", ".join(sorted(unknown)) + ".")
        with self.db.tx() as c:
            tree = _Tree(c)
            node = tree.node(group_id)
            name = node.name
            parent_id = node.parent_id
            if "name" in changes:
                name = clean_name(changes["name"], "folder")
            if "parent_id" in changes:
                parent_id = changes["parent_id"]
                self._check_move(tree, group_id, parent_id)
            if name.casefold() in tree.sibling_names(parent_id, exclude=group_id):
                raise _name_taken(name)
            sort_order = node.sort_order
            if "sort_order" in changes:
                value = changes["sort_order"]
                if not isinstance(value, int) or isinstance(value, bool):
                    raise Invalid("Sort order must be a whole number.")
                sort_order = value
            self._write(
                c,
                "UPDATE groups SET name = ?, parent_id = ?, sort_order = ? WHERE id = ?",
                (name, parent_id, sort_order, group_id),
                name,
            )
            return _Tree(c).record(group_id)

    def delete(self, group_id: str) -> None:
        with self.db.tx() as c:
            tree = _Tree(c)
            node = tree.node(group_id)
            parent = node.parent_id
            # Free the folder's own name first so a child with the same name can take it.
            c.execute("UPDATE groups SET name = ? WHERE id = ?", (group_id, group_id))
            taken = tree.sibling_names(parent, exclude=group_id)
            kids = sorted(
                tree.children.get(group_id, []),
                key=lambda k: (tree.nodes[k].sort_order, tree.nodes[k].name.casefold()),
            )
            for kid in kids:
                new_name = self._free_name(tree.nodes[kid].name, taken)
                taken.add(new_name.casefold())
                c.execute(
                    "UPDATE groups SET parent_id = ?, name = ? WHERE id = ?",
                    (parent, new_name, kid),
                )
            c.execute(
                "UPDATE connections SET group_id = ?, updated_at = ? WHERE group_id = ?",
                (parent, now_iso(), group_id),
            )
            c.execute("DELETE FROM groups WHERE id = ?", (group_id,))

    # -- internals ------------------------------------------------------------------------------

    def _check_depth(self, level: int) -> None:
        if level > self.limits.max_group_depth:
            raise Invalid(
                f"Folders can be at most {self.limits.max_group_depth} levels deep.",
                code=ErrorCode.GROUP_TOO_DEEP,
            )

    def _check_move(self, tree: _Tree, group_id: str, parent_id: object) -> None:
        if parent_id is None:
            self._check_depth(tree.height(group_id))
            return
        if not isinstance(parent_id, str) or parent_id not in tree.nodes:
            raise _parent_unknown()
        if parent_id in tree.subtree(group_id):
            raise Invalid(
                "A folder can't be moved into itself or into one of its own folders.",
                code=ErrorCode.GROUP_CYCLE,
            )
        self._check_depth(tree.level(parent_id) + tree.height(group_id))

    @staticmethod
    def _free_name(name: str, taken: set[str]) -> str:
        if name.casefold() not in taken:
            return name
        n = 2
        while (candidate := suffixed(name, f" ({n})")).casefold() in taken:
            n += 1
        return candidate

    @staticmethod
    def _write(c: sqlite3.Connection, sql: str, params: tuple[Any, ...], name: str) -> None:
        try:
            c.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            if is_unique_violation(exc):
                raise _name_taken(name) from None
            raise Invalid("Those folder settings are not valid.") from None
