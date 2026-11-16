"""SQLite schema, ``PRAGMA user_version`` migrations and the connection helper (WAL).

Frozen by package 0. Package 2 may append migrations (never edit a released one); any schema
change is a contract change and goes through the package-0 owner.

One connection per process, shared by threads behind a re-entrant lock: Port-Key has one user
and every statement is short. Use ``with db.tx() as c:`` for writes (BEGIN IMMEDIATE ... COMMIT,
rollback on exception; nested ``tx()`` joins the outer transaction) and ``db.query`` /
``db.query_one`` for reads. Timestamps are ISO-8601 UTC text, e.g. ``2026-11-15T14:02:00Z``.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_V1 = """
CREATE TABLE groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 64),
    parent_id   TEXT REFERENCES groups(id) ON DELETE RESTRICT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX groups_sibling_name ON groups (coalesce(parent_id, ''), name COLLATE NOCASE);
CREATE INDEX groups_parent ON groups (parent_id);

CREATE TABLE connections (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 64),
    host          TEXT NOT NULL CHECK (length(host) BETWEEN 1 AND 253),
    port          INTEGER NOT NULL DEFAULT 22 CHECK (port BETWEEN 1 AND 65535),
    username      TEXT NOT NULL CHECK (length(username) BETWEEN 1 AND 128),
    auth_method   TEXT NOT NULL CHECK (auth_method IN ('key', 'password')),
    key_fp        TEXT,
    jump_id       TEXT REFERENCES connections(id) ON DELETE RESTRICT,
    group_id      TEXT REFERENCES groups(id) ON DELETE RESTRICT,
    color         TEXT NOT NULL DEFAULT 'grey'
                  CHECK (color IN ('red', 'amber', 'green', 'blue', 'purple', 'grey')),
    pinned        INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    notes         TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_used_at  TEXT,
    use_count     INTEGER NOT NULL DEFAULT 0,
    CHECK (jump_id IS NULL OR jump_id <> id)
);
CREATE UNIQUE INDEX connections_name ON connections (name COLLATE NOCASE);
CREATE INDEX connections_jump ON connections (jump_id);
CREATE INDEX connections_group ON connections (group_id);
CREATE INDEX connections_key ON connections (key_fp);

CREATE TABLE tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE
                CHECK (name = lower(name) AND length(name) BETWEEN 1 AND 32),
    created_at  TEXT NOT NULL
);

CREATE TABLE connection_tags (
    connection_id  TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (connection_id, tag_id)
) WITHOUT ROWID;
CREATE INDEX connection_tags_tag ON connection_tags (tag_id);

-- A tag disappears when its last use is removed (also on cascade from connections).
CREATE TRIGGER connection_tags_cleanup AFTER DELETE ON connection_tags
BEGIN
    DELETE FROM tags
     WHERE id = OLD.tag_id
       AND NOT EXISTS (SELECT 1 FROM connection_tags WHERE tag_id = OLD.tag_id);
END;

CREATE TABLE keys_meta (
    fp          TEXT PRIMARY KEY,
    label       TEXT,
    last_path   TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('ssh_dir', 'generated')),
    hidden      INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    created_at  TEXT NOT NULL
);

CREATE TABLE host_keys (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    host           TEXT NOT NULL CHECK (host = lower(host)),
    port           INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    key_type       TEXT NOT NULL,
    public_key     TEXT NOT NULL,
    fp_sha256      TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    UNIQUE (host, port, public_key)
);
CREATE INDEX host_keys_host ON host_keys (host, port);

CREATE TABLE dismissed_suggestions (
    host          TEXT NOT NULL CHECK (host = lower(host)),
    port          INTEGER NOT NULL,
    dismissed_at  TEXT NOT NULL,
    PRIMARY KEY (host, port)
) WITHOUT ROWID;

CREATE TABLE app_auth (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    salt        BLOB NOT NULL,
    hash        BLOB NOT NULL,
    n           INTEGER NOT NULL,
    r           INTEGER NOT NULL,
    p           INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE tokens (
    token_hash    TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    last_used_at  TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX tokens_expiry ON tokens (expires_at);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL CHECK (json_valid(value))
) WITHOUT ROWID;

CREATE TABLE events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    kind           TEXT NOT NULL,
    connection_id  TEXT,
    detail         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail))
);
CREATE INDEX events_connection ON events (connection_id);

-- Keep the newest 5,000 events.
CREATE TRIGGER events_cap AFTER INSERT ON events
BEGIN
    DELETE FROM events WHERE id <= NEW.id - 5000;
END;
"""

# Version 2 (security review, 2026-11-16): host-key records are scoped by route and can
# distrust a known_hosts key.
# - ``via``: the id of the jump connection the host is reached through, '' when direct. One
#   address behind two jump hosts can be two machines, so each route has its own trust.
#   Existing records become direct ones. Not a foreign key: deleting a jump connection must not
#   be blocked by its records, and an Undo restores the same id.
# - ``distrusted``: 1 for a known_hosts key the owner replaced in Port-Key. known_hosts is never
#   edited, so without this the replaced (maybe compromised) key would stay trusted.
SCHEMA_V2 = """
CREATE TABLE host_keys_v2 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    host           TEXT NOT NULL CHECK (host = lower(host)),
    port           INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    via            TEXT NOT NULL DEFAULT '',
    key_type       TEXT NOT NULL,
    public_key     TEXT NOT NULL,
    fp_sha256      TEXT NOT NULL,
    distrusted     INTEGER NOT NULL DEFAULT 0 CHECK (distrusted IN (0, 1)),
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    UNIQUE (host, port, via, public_key)
);
INSERT INTO host_keys_v2 (id, host, port, key_type, public_key, fp_sha256, first_seen_at,
                          last_seen_at)
    SELECT id, host, port, key_type, public_key, fp_sha256, first_seen_at, last_seen_at
      FROM host_keys;
DROP TABLE host_keys;
ALTER TABLE host_keys_v2 RENAME TO host_keys;
CREATE INDEX host_keys_host ON host_keys (host, port, via);
"""

# (version, script). Append only; a released entry is never edited.
MIGRATIONS: tuple[tuple[int, str], ...] = ((1, SCHEMA_V1), (2, SCHEMA_V2))

SCHEMA_VERSION = MIGRATIONS[-1][0]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def now_iso() -> str:
    return to_iso(utcnow())


class Database:
    """Thread-safe wrapper around one ``sqlite3.Connection`` in WAL mode."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._depth = 0

    # -- lifecycle --------------------------------------------------------------------------

    def open(self) -> None:
        """Open the file (creating it) and set pragmas. Idempotent."""
        with self._lock:
            if self._conn is not None:
                return
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not open")
        return self._conn

    # -- migrations -------------------------------------------------------------------------

    def user_version(self) -> int:
        with self._lock:
            return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Apply every migration newer than ``user_version``, each in its own transaction.
        Returns the resulting version. Refuses a database newer than this code."""
        self.open()
        with self._lock:
            current = self.user_version()
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {current} is newer than this Port-Key ({SCHEMA_VERSION})"
                )
            for version, script in MIGRATIONS:
                if version <= current:
                    continue
                try:
                    self.conn.executescript(
                        f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                    )
                except sqlite3.Error:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise
                current = version
            return current

    # -- access -----------------------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Nested calls join the outermost one."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self.conn.execute("COMMIT")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self.conn.execute(sql, params).fetchone()
            return row

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """One statement in its own transaction (or the caller's, inside ``tx()``)."""
        with self.tx() as c:
            return c.execute(sql, params)

    def tables(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        return [r["name"] for r in rows if not r["name"].startswith("sqlite_")]
