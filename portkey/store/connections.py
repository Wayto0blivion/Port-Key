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

Every method is one short transaction on the shared ``Database`` (see ``store.db``): checks and
writes run inside the same ``tx()`` so they see the same data.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from typing import Any

from portkey.config import Limits
from portkey.errors import Conflict, ErrorCode, Invalid, NotFound
from portkey.models import BulkChange, ConnectionDraft, ConnectionRecord, ConnectionRef
from portkey.store import _jumps
from portkey.store._common import (
    check_tag_count,
    clean_name,
    clean_tags,
    is_unique_violation,
    suffixed,
)
from portkey.store._conn_rows import (
    COLUMNS,
    EDITABLE,
    add_links,
    check_keys,
    clean_field,
    ensure_tag,
    set_links,
    tags_by_connection,
    tags_of,
    to_record,
)
from portkey.store.db import Database, now_iso, to_iso

_ORDER = "ORDER BY pinned DESC, sort_order, name COLLATE NOCASE, id"

# Undo after a delete: how long, and how many, deleted connections are kept in memory.
DELETED_TTL = 10 * 60.0
DELETED_KEEP = 50


def _links_for(c: sqlite3.Connection, id_json: str) -> dict[str, set[str]]:
    """Tag names per connection for the ids in a JSON array."""
    out: dict[str, set[str]] = {}
    rows = c.execute(
        "SELECT ct.connection_id, t.name FROM connection_tags ct JOIN tags t ON t.id = ct.tag_id"
        " WHERE ct.connection_id IN (SELECT value FROM json_each(?))",
        (id_json,),
    )
    for cid, name in rows:
        out.setdefault(cid, set()).add(name)
    return out


def _not_found() -> NotFound:
    return NotFound("That connection doesn't exist any more.")


def _name_taken(name: str) -> Conflict:
    return Conflict(f"A connection named {name} already exists.", code=ErrorCode.NAME_TAKEN)


def _group_unknown() -> Invalid:
    return Invalid("That folder doesn't exist any more.", code=ErrorCode.GROUP_UNKNOWN)


class SqliteConnectionsRepo:
    def __init__(self, db: Database, limits: Limits) -> None:
        self.db = db
        self.limits = limits
        # Deleted records for Undo (restore), oldest first; guarded by the database lock.
        self._deleted: dict[str, tuple[ConnectionRecord, float]] = {}

    # -- reads ----------------------------------------------------------------------------------

    def list_all(self) -> list[ConnectionRecord]:
        with self.db.tx() as c:
            tags = tags_by_connection(c)
            rows = c.execute(f"SELECT {COLUMNS} FROM connections {_ORDER}").fetchall()  # noqa: S608
        return [to_record(r, tags.get(r["id"], ())) for r in rows]

    def get(self, conn_id: str) -> ConnectionRecord:
        found = self.find(conn_id)
        if found is None:
            raise _not_found()
        return found

    def find(self, conn_id: str) -> ConnectionRecord | None:
        with self.db.tx() as c:
            return self._find(c, conn_id)

    def dependants(self, conn_id: str) -> list[ConnectionRef]:
        with self.db.tx() as c:
            return self._dependants(c, conn_id)

    def resolve_chain(self, conn_id: str) -> list[ConnectionRecord]:
        with self.db.tx() as c:
            jumps = _jumps.jump_map(c)
            if conn_id not in jumps:
                raise _not_found()
            ids = list(reversed(_jumps.chain_up(jumps, conn_id)))
            records = [self._find(c, i) for i in ids]
        return [r for r in records if r is not None]

    def validate_jump(self, conn_id: str | None, jump_id: str | None) -> None:
        with self.db.tx() as c:
            _jumps.validate_jump(c, conn_id, jump_id, self.limits.max_jump_hops)

    def using_key(self, fp: str, default_fp: str | None) -> list[ConnectionRef]:
        with self.db.tx() as c:
            return self._using_key(c, fp, default_fp)

    # -- writes ---------------------------------------------------------------------------------

    def create(self, draft: ConnectionDraft) -> ConnectionRecord:
        values: dict[str, Any] = {k: getattr(draft, k) for k in EDITABLE if k != "sort_order"}
        values["tags"] = draft.tags
        with self.db.tx() as c:
            clean = self._prepare(c, None, values)
            tags = clean.pop("tags")
            conn_id = str(uuid.uuid4())
            ts = now_iso()
            clean.update(id=conn_id, created_at=ts, updated_at=ts, pinned=int(clean["pinned"]))
            # Column names come from the EDITABLE whitelist, never from input.
            cols = ", ".join(clean)
            marks = ", ".join("?" * len(clean))
            sql = f"INSERT INTO connections ({cols}) VALUES ({marks})"  # noqa: S608
            self._write(c, sql, list(clean.values()))
            add_links(c, conn_id, tags)
            return self._get(c, conn_id)

    def update(self, conn_id: str, changes: Mapping[str, Any]) -> ConnectionRecord:
        check_keys(changes)
        with self.db.tx() as c:
            self._get(c, conn_id)
            clean = self._prepare(c, conn_id, dict(changes))
            tags = clean.pop("tags", None)
            if "pinned" in clean:
                clean["pinned"] = int(clean["pinned"])
            clean["updated_at"] = now_iso()
            sets = ", ".join(f"{k} = ?" for k in clean)
            sql = f"UPDATE connections SET {sets} WHERE id = ?"  # noqa: S608
            self._write(c, sql, [*clean.values(), conn_id])
            if tags is not None:
                set_links(c, conn_id, tags)
            return self._get(c, conn_id)

    def delete(self, conn_id: str) -> None:
        with self.db.tx() as c:
            record = self._get(c, conn_id)
            deps = self._dependants(c, conn_id)
            if deps:
                names = ", ".join(d.name for d in deps)
                noun = "connection" if len(deps) == 1 else "connections"
                raise Conflict(
                    f"{record.name} is the jump host for {len(deps)} {noun}: {names}. "
                    "Change their Jump via setting first.",
                    code=ErrorCode.JUMP_IN_USE,
                    dependants=[{"id": d.id, "name": d.name} for d in deps],
                )
            c.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
            self._remember_deleted(record)

    def restore(self, conn_id: str) -> ConnectionRecord:
        with self.db.tx() as c:
            self._forget_old_deleted()
            kept = self._deleted.get(conn_id)
            if (
                kept is None
                or c.execute("SELECT 1 FROM connections WHERE id = ?", (conn_id,)).fetchone()
            ):
                raise NotFound("There is nothing to undo for that connection any more.")
            record = kept[0]
            self._check_name_free(c, record.name, None)
            _jumps.validate_jump(c, conn_id, record.jump_id, self.limits.max_jump_hops)
            group_id = record.group_id
            if (
                group_id is not None
                and c.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone() is None
            ):
                group_id = None  # its folder was deleted meanwhile: Ungrouped
            self._write(
                c,
                f"INSERT INTO connections ({COLUMNS}) VALUES ({', '.join('?' * 17)})",  # noqa: S608
                [
                    record.id,
                    record.name,
                    record.host,
                    record.port,
                    record.username,
                    record.auth_method,
                    record.key_fp,
                    record.jump_id,
                    group_id,
                    record.color,
                    int(record.pinned),
                    record.notes,
                    record.sort_order,
                    to_iso(record.created_at),
                    now_iso(),
                    None if record.last_used_at is None else to_iso(record.last_used_at),
                    record.use_count,
                ],
            )
            add_links(c, record.id, record.tags)
            del self._deleted[conn_id]
            return self._get(c, conn_id)

    def _remember_deleted(self, record: ConnectionRecord) -> None:
        """Keep a deleted record for ``restore`` (call inside the delete's transaction)."""
        self._forget_old_deleted()
        self._deleted[record.id] = (record, time.monotonic())
        while len(self._deleted) > DELETED_KEEP:
            self._deleted.pop(next(iter(self._deleted)))

    def _forget_old_deleted(self) -> None:
        cutoff = time.monotonic() - DELETED_TTL
        for conn_id, (_, at) in list(self._deleted.items()):
            if at < cutoff:
                del self._deleted[conn_id]

    def duplicate(self, conn_id: str) -> ConnectionRecord:
        with self.db.tx() as c:
            src = self._get(c, conn_id)
            taken = {r[0].casefold() for r in c.execute("SELECT name FROM connections")}
            n = 1
            while True:
                name = suffixed(src.name, " (copy)" if n == 1 else f" (copy {n})")
                if name.casefold() not in taken:
                    break
                n += 1
            new_id = str(uuid.uuid4())
            ts = now_iso()
            c.execute(
                "INSERT INTO connections (id, name, host, port, username, auth_method, key_fp,"
                " jump_id, group_id, color, pinned, notes, sort_order, created_at, updated_at)"
                " SELECT ?, ?, host, port, username, auth_method, key_fp, jump_id, group_id,"
                " color, 0, notes, sort_order, ?, ? FROM connections WHERE id = ?",
                (new_id, name, ts, ts, conn_id),
            )
            add_links(c, new_id, src.tags)
            return self._get(c, new_id)

    def touch_used(self, conn_id: str) -> None:
        self.db.execute(
            "UPDATE connections SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (now_iso(), conn_id),
        )

    def reassign_key(
        self, fp: str, default_fp: str | None, auth_method: str, key_fp: str | None
    ) -> list[ConnectionRef]:
        method = clean_field("auth_method", auth_method)
        new_fp = None if method == "password" else clean_field("key_fp", key_fp)
        with self.db.tx() as c:
            refs = self._using_key(c, fp, default_fp)
            ts = now_iso()
            c.executemany(
                "UPDATE connections SET auth_method = ?, key_fp = ?, updated_at = ? WHERE id = ?",
                [(method, new_fp, ts, r.id) for r in refs],
            )
            return refs

    def bulk_update(self, change: BulkChange) -> list[ConnectionRecord]:
        ids = list(dict.fromkeys(change.ids))
        if not ids or len(ids) > self.limits.bulk_max_ids:
            raise Invalid(f"Select 1 to {self.limits.bulk_max_ids} connections.")
        add = set(clean_tags(change.add_tags))
        remove = set(clean_tags(change.remove_tags))
        add -= remove
        # Set-based statements over the ids bound as one JSON array keep the transaction (and
        # the database lock async callers may be waiting on) short even for 1000 connections.
        id_json = json.dumps(ids)
        with self.db.tx() as c:
            found = c.execute(
                "SELECT count(*) FROM connections WHERE id IN (SELECT value FROM json_each(?))",
                (id_json,),
            ).fetchone()[0]
            if len(ids) - int(found):
                raise NotFound(
                    f"{len(ids) - int(found)} of the selected connections no longer exist. "
                    "Nothing was changed."
                )
            if change.set_group and change.group_id is not None:
                self._check_group(c, change.group_id)
            if add:
                current = _links_for(c, id_json)
                for conn_id in ids:
                    check_tag_count((current.get(conn_id, set()) | add) - remove)
            if change.set_group:
                c.execute(
                    "UPDATE connections SET group_id = ?"
                    " WHERE id IN (SELECT value FROM json_each(?))",
                    (change.group_id, id_json),
                )
            if remove:
                c.execute(
                    "DELETE FROM connection_tags"
                    " WHERE connection_id IN (SELECT value FROM json_each(?))"
                    " AND tag_id IN (SELECT id FROM tags"
                    " WHERE name IN (SELECT value FROM json_each(?)))",
                    (id_json, json.dumps(sorted(remove))),
                )
            for name in sorted(add):
                c.execute(
                    "INSERT OR IGNORE INTO connection_tags (connection_id, tag_id)"
                    " SELECT value, ? FROM json_each(?)",
                    (ensure_tag(c, name), id_json),
                )
            c.execute(
                "UPDATE connections SET updated_at = ?"
                " WHERE id IN (SELECT value FROM json_each(?))",
                (now_iso(), id_json),
            )
            sql = f"SELECT {COLUMNS} FROM connections WHERE id IN (SELECT value FROM json_each(?))"  # noqa: S608
            by_id = {r["id"]: r for r in c.execute(sql, (id_json,))}
            tags = _links_for(c, id_json)
        return [to_record(by_id[i], tags.get(i, ())) for i in ids]

    # -- internals ------------------------------------------------------------------------------

    def _prepare(
        self, c: sqlite3.Connection, conn_id: str | None, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate the given fields against the rules and the database; returns cleaned
        values (``tags`` as a sorted tuple when present)."""
        clean: dict[str, Any] = {}
        if "name" in values:
            clean["name"] = clean_name(values["name"], "connection")
            self._check_name_free(c, clean["name"], conn_id)
        for key in EDITABLE:
            if key != "name" and key in values:
                clean[key] = clean_field(key, values[key])
        if clean.get("group_id") is not None:
            self._check_group(c, clean["group_id"])
        if "jump_id" in clean:
            _jumps.validate_jump(c, conn_id, clean["jump_id"], self.limits.max_jump_hops)
        if "tags" in values:
            if values["tags"] is None:
                raise Invalid("tags can't be empty; send [] to clear them.")
            clean["tags"] = clean_tags(values["tags"])
            check_tag_count(clean["tags"])
        return clean

    def _check_name_free(self, c: sqlite3.Connection, name: str, conn_id: str | None) -> None:
        # Python's casefold also catches non-ASCII case variants that NOCASE misses.
        folded = name.casefold()
        for other_id, other_name in c.execute("SELECT id, name FROM connections"):
            if other_id != conn_id and other_name.casefold() == folded:
                raise _name_taken(name)

    def _check_group(self, c: sqlite3.Connection, group_id: str) -> None:
        if c.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone() is None:
            raise _group_unknown()

    def _write(self, c: sqlite3.Connection, sql: str, params: list[Any]) -> None:
        try:
            c.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            if is_unique_violation(exc):
                raise Conflict(
                    "A connection with that name already exists.", code=ErrorCode.NAME_TAKEN
                ) from None
            raise Invalid("Those connection settings are not valid.") from None

    def _find(self, c: sqlite3.Connection, conn_id: str) -> ConnectionRecord | None:
        row = c.execute(f"SELECT {COLUMNS} FROM connections WHERE id = ?", (conn_id,)).fetchone()  # noqa: S608
        return None if row is None else to_record(row, tags_of(c, conn_id))

    def _get(self, c: sqlite3.Connection, conn_id: str) -> ConnectionRecord:
        found = self._find(c, conn_id)
        if found is None:
            raise _not_found()
        return found

    def _dependants(self, c: sqlite3.Connection, conn_id: str) -> list[ConnectionRef]:
        rows = c.execute(
            "SELECT id, name FROM connections WHERE jump_id = ? ORDER BY name COLLATE NOCASE",
            (conn_id,),
        )
        return [ConnectionRef(id=r[0], name=r[1]) for r in rows]

    def _using_key(
        self, c: sqlite3.Connection, fp: str, default_fp: str | None
    ) -> list[ConnectionRef]:
        rows = c.execute(
            "SELECT id, name FROM connections WHERE auth_method = 'key'"
            " AND (key_fp = ? OR (key_fp IS NULL AND ? = ?)) ORDER BY name COLLATE NOCASE",
            (fp, fp, default_fp),
        )
        return [ConnectionRef(id=r[0], name=r[1]) for r in rows]
