"""The schema migrates, is idempotent, and its triggers and constraints hold."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portkey.store.db import SCHEMA_VERSION, Database, now_iso

TABLES = {
    "app_auth",
    "connection_tags",
    "connections",
    "dismissed_suggestions",
    "events",
    "groups",
    "host_keys",
    "keys_meta",
    "settings",
    "tags",
    "tokens",
}


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "portkey.db")
    d.migrate()
    return d


def _conn(db: Database, cid: str, name: str, group_id: str | None = None) -> None:
    ts = now_iso()
    db.execute(
        "INSERT INTO connections (id, name, host, username, auth_method, group_id, created_at,"
        " updated_at) VALUES (?, ?, '192.168.10.42', 'john', 'key', ?, ?, ?)",
        (cid, name, group_id, ts, ts),
    )


def test_migrate_creates_schema_and_is_idempotent(db: Database) -> None:
    assert db.user_version() == SCHEMA_VERSION
    assert set(db.tables()) == TABLES
    assert db.migrate() == SCHEMA_VERSION
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    assert db.query_one("PRAGMA foreign_keys")[0] == 1


def test_refuses_newer_database(db: Database) -> None:
    db.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        db.migrate()


def test_connection_names_unique_case_insensitive(db: Database) -> None:
    _conn(db, "c1", "web-prod")
    with pytest.raises(sqlite3.IntegrityError):
        _conn(db, "c2", "WEB-PROD")


def test_group_sibling_names_unique(db: Database) -> None:
    ts = now_iso()
    sql = "INSERT INTO groups (id, name, parent_id, created_at) VALUES (?, ?, ?, ?)"
    db.execute(sql, ("g1", "Office LAN", None, ts))
    db.execute(sql, ("g2", "Servers", "g1", ts))
    db.execute(sql, ("g3", "Servers", None, ts))  # same name, different parent: fine
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("g4", "office lan", None, ts))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("g5", "SERVERS", "g1", ts))


def test_group_with_connections_cannot_be_deleted_directly(db: Database) -> None:
    db.execute("INSERT INTO groups (id, name, created_at) VALUES ('g1', 'VPN', ?)", (now_iso(),))
    _conn(db, "c1", "vpn-gw", "g1")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM groups WHERE id = 'g1'")


def test_tag_removed_with_last_use(db: Database) -> None:
    _conn(db, "c1", "web-prod")
    _conn(db, "c2", "build-box")
    db.execute("INSERT INTO tags (name, created_at) VALUES ('prod', ?)", (now_iso(),))
    tag_id = db.query_one("SELECT id FROM tags WHERE name = 'prod'")[0]
    for cid in ("c1", "c2"):
        db.execute("INSERT INTO connection_tags VALUES (?, ?)", (cid, tag_id))
    db.execute("DELETE FROM connections WHERE id = 'c1'")  # cascade fires the cleanup trigger
    assert db.query_one("SELECT count(*) FROM tags")[0] == 1
    db.execute("DELETE FROM connection_tags WHERE connection_id = 'c2'")
    assert db.query_one("SELECT count(*) FROM tags")[0] == 0


def test_tags_must_be_lower_case(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tags (name, created_at) VALUES ('Prod', ?)", (now_iso(),))


def test_jump_in_use_is_restricted(db: Database) -> None:
    _conn(db, "c1", "build-box")
    _conn(db, "c2", "web-prod")
    db.execute("UPDATE connections SET jump_id = 'c1' WHERE id = 'c2'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM connections WHERE id = 'c1'")


def test_events_capped_at_5000(db: Database) -> None:
    with db.tx() as c:
        c.executemany(
            "INSERT INTO events (ts, kind) VALUES (?, 'session_opened')",
            [(now_iso(),)] * 5010,
        )
    assert db.query_one("SELECT count(*) FROM events")[0] == 5000


def _insert_then_fail(db: Database) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO settings VALUES ('font_size', '14')")
        raise RuntimeError


def test_tx_rolls_back(db: Database) -> None:
    with pytest.raises(RuntimeError):
        _insert_then_fail(db)
    assert db.query_one("SELECT count(*) FROM settings")[0] == 0
