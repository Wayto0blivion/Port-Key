"""Tags repository (package 2). Implements ``interfaces.TagsRepo``.

Tags are stored lower-case and match ``models.TAG_PATTERN``. They are created by
``ConnectionsRepo`` on first assignment and deleted by the ``connection_tags_cleanup`` trigger
when their last use goes. ``rename`` onto an existing name merges the two (one transaction).

Names passed in (including the old name from a URL) are normalised the same way as input, so
``#Prod`` finds ``prod``. Connections whose tags change get a new ``updated_at``.
"""

from __future__ import annotations

import sqlite3

from portkey.errors import ErrorCode, NotFound
from portkey.interfaces import normalize_tag
from portkey.models import TagCount
from portkey.store._common import clean_tag
from portkey.store.db import Database, now_iso


def _tag_id(c: sqlite3.Connection, name: str) -> int:
    row = c.execute("SELECT id FROM tags WHERE name = ?", (normalize_tag(name),)).fetchone()
    if row is None:
        raise NotFound("That tag isn't used by any connection.", code=ErrorCode.NOT_FOUND)
    return int(row[0])


def _count(c: sqlite3.Connection, tag_id: int) -> int:
    row = c.execute("SELECT count(*) FROM connection_tags WHERE tag_id = ?", (tag_id,)).fetchone()
    return int(row[0])


def _touch_users(c: sqlite3.Connection, tag_id: int) -> None:
    c.execute(
        "UPDATE connections SET updated_at = ?"
        " WHERE id IN (SELECT connection_id FROM connection_tags WHERE tag_id = ?)",
        (now_iso(), tag_id),
    )


class SqliteTagsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[TagCount]:
        rows = self.db.query(
            "SELECT t.name, count(*) AS n FROM tags t JOIN connection_tags ct ON ct.tag_id = t.id"
            " GROUP BY t.id ORDER BY n DESC, t.name"
        )
        return [TagCount(name=r[0], count=int(r[1])) for r in rows]

    def rename(self, old: str, new: str) -> TagCount:
        target = clean_tag(new)
        with self.db.tx() as c:
            old_id = _tag_id(c, old)
            _touch_users(c, old_id)
            row = c.execute("SELECT id FROM tags WHERE name = ?", (target,)).fetchone()
            if row is None:
                c.execute("UPDATE tags SET name = ? WHERE id = ?", (target, old_id))
                return TagCount(name=target, count=_count(c, old_id))
            new_id = int(row[0])
            if new_id != old_id:
                # Merge: link every user of the old tag to the new one, then drop the old links
                # (the cleanup trigger removes the old tag row).
                c.execute(
                    "INSERT OR IGNORE INTO connection_tags (connection_id, tag_id)"
                    " SELECT connection_id, ? FROM connection_tags WHERE tag_id = ?",
                    (new_id, old_id),
                )
                c.execute("DELETE FROM connection_tags WHERE tag_id = ?", (old_id,))
            return TagCount(name=target, count=_count(c, new_id))

    def delete(self, name: str) -> None:
        with self.db.tx() as c:
            tag_id = _tag_id(c, name)
            _touch_users(c, tag_id)
            c.execute("DELETE FROM connection_tags WHERE tag_id = ?", (tag_id,))
            c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
