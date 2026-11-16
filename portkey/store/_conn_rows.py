"""Row mapping, field checks and tag links for the connections repository.

The API schema validates requests first; these checks repeat the rules for callers that build a
``ConnectionDraft`` themselves (the wizard) so bad data never reaches the table.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from portkey.errors import Invalid
from portkey.models import COLORS, ConnectionRecord
from portkey.store._common import opt_iso
from portkey.store.db import from_iso, now_iso

COLUMNS = (
    "id, name, host, port, username, auth_method, key_fp, jump_id, group_id, color, pinned,"
    " notes, sort_order, created_at, updated_at, last_used_at, use_count"
)

# Fields a draft or a PATCH may carry, besides ``tags``.
EDITABLE = (
    "name",
    "host",
    "port",
    "username",
    "auth_method",
    "key_fp",
    "jump_id",
    "group_id",
    "color",
    "pinned",
    "notes",
    "sort_order",
)
NULLABLE = frozenset({"key_fp", "jump_id", "group_id"})

_HOST = re.compile(r"^[^\s@/]{1,253}$")
_USER = re.compile(r"^\S{1,128}$")
_FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def to_record(row: sqlite3.Row, tags: Iterable[str]) -> ConnectionRecord:
    return ConnectionRecord(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        port=int(row["port"]),
        username=row["username"],
        auth_method=row["auth_method"],
        key_fp=row["key_fp"],
        jump_id=row["jump_id"],
        group_id=row["group_id"],
        tags=tuple(sorted(tags)),
        color=row["color"],
        pinned=bool(row["pinned"]),
        notes=row["notes"],
        sort_order=int(row["sort_order"]),
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
        last_used_at=opt_iso(row["last_used_at"]),
        use_count=int(row["use_count"]),
    )


def _bad(message: str) -> Invalid:
    return Invalid(message)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def clean_field(key: str, value: Any) -> Any:
    """Check and normalise one editable field (not ``name``, which needs the uniqueness check,
    and not the reference fields, which need the database)."""
    if value is None:
        if key in NULLABLE:
            return None
        raise _bad(f"{key} can't be empty.")
    if key == "host":
        host = value.strip() if isinstance(value, str) else ""
        if not _HOST.match(host):
            raise _bad("Host must be a name or address without spaces, @ or /.")
        return host
    if key == "port":
        if not _int(value) or not 1 <= value <= 65535:
            raise _bad("Port must be a number from 1 to 65535.")
        return value
    if key == "username":
        user = value.strip() if isinstance(value, str) else ""
        if not _USER.match(user):
            raise _bad("User must be 1 to 128 characters without spaces.")
        return user
    if key == "auth_method":
        if value not in ("key", "password"):
            raise _bad("Sign-in method must be key or password.")
        return value
    if key == "key_fp":
        if not isinstance(value, str) or not _FP.match(value):
            raise _bad("Key fingerprints look like SHA256: followed by 43 characters.")
        return value
    if key == "color":
        if value not in COLORS:
            raise _bad("Colour must be one of " + ", ".join(COLORS) + ".")
        return value
    if key == "pinned":
        if not isinstance(value, bool):
            raise _bad("Pinned must be true or false.")
        return value
    if key == "notes":
        if not isinstance(value, str) or len(value) > 2000:
            raise _bad("Notes can be at most 2000 characters.")
        return value
    if key == "sort_order":
        if not _int(value):
            raise _bad("Sort order must be a whole number.")
        return value
    if key in ("jump_id", "group_id"):
        if not isinstance(value, str):
            raise _bad(f"{key} must be an id.")
        return value
    raise _bad(f"{key} is not a connection field.")


def check_keys(changes: Mapping[str, Any]) -> None:
    unknown = set(changes) - set(EDITABLE) - {"tags"}
    if unknown:
        raise _bad("Unknown connection field: " + ", ".join(sorted(unknown)) + ".")


# --- tag links --------------------------------------------------------------------------------


def ensure_tag(c: sqlite3.Connection, name: str) -> int:
    """The id of tag ``name`` (already normalised), creating it on first use."""
    c.execute(
        "INSERT INTO tags (name, created_at) VALUES (?, ?) ON CONFLICT (name) DO NOTHING",
        (name, now_iso()),
    )
    row = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return int(row[0])


def tags_of(c: sqlite3.Connection, conn_id: str) -> set[str]:
    rows = c.execute(
        "SELECT t.name FROM connection_tags ct JOIN tags t ON t.id = ct.tag_id"
        " WHERE ct.connection_id = ?",
        (conn_id,),
    )
    return {r[0] for r in rows}


def tags_by_connection(c: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = c.execute(
        "SELECT ct.connection_id, t.name FROM connection_tags ct JOIN tags t ON t.id = ct.tag_id"
    )
    for cid, name in rows:
        out.setdefault(cid, []).append(name)
    return out


def add_links(c: sqlite3.Connection, conn_id: str, names: Iterable[str]) -> None:
    for name in names:
        c.execute(
            "INSERT OR IGNORE INTO connection_tags (connection_id, tag_id) VALUES (?, ?)",
            (conn_id, ensure_tag(c, name)),
        )


def remove_links(c: sqlite3.Connection, conn_id: str, names: Iterable[str]) -> None:
    """Unlink tags; the schema's trigger deletes a tag when its last link goes."""
    for name in names:
        c.execute(
            "DELETE FROM connection_tags WHERE connection_id = ?"
            " AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            (conn_id, name),
        )


def set_links(c: sqlite3.Connection, conn_id: str, want: Iterable[str]) -> bool:
    """Make the connection's tags exactly ``want`` by diffing, so tags that stay keep their
    rows. Returns whether anything changed."""
    have = tags_of(c, conn_id)
    target = set(want)
    remove_links(c, conn_id, sorted(have - target))
    add_links(c, conn_id, sorted(target - have))
    return have != target
