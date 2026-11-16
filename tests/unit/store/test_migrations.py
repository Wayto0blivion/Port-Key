"""Migrations from an empty file, reopening an existing file, and data surviving a restart."""

from __future__ import annotations

from pathlib import Path

from portkey.config import Limits
from portkey.models import ConnectionDraft
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import SCHEMA_VERSION, Database
from portkey.store.settings import SqliteSettingsRepo


def test_empty_file_migrates_and_repos_work(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "portkey.db"
    db = Database(path)
    assert db.migrate() == SCHEMA_VERSION
    assert path.exists()
    repo = SqliteConnectionsRepo(db, Limits())
    assert repo.list_all() == []
    db.close()


def test_existing_zero_byte_file_migrates(tmp_path: Path) -> None:
    path = tmp_path / "portkey.db"
    path.touch()
    db = Database(path)
    assert db.migrate() == SCHEMA_VERSION
    db.close()


def test_data_survives_reopen_and_second_migrate(tmp_path: Path) -> None:
    path = tmp_path / "portkey.db"
    db = Database(path)
    db.migrate()
    rec = SqliteConnectionsRepo(db, Limits()).create(
        ConnectionDraft(
            name="web-prod",
            host="192.168.10.42",
            username="john",
            auth_method="key",
            tags=("prod",),
        )
    )
    SqliteSettingsRepo(db).update({"font_size": 18})
    db.close()

    again = Database(path)
    assert again.migrate() == SCHEMA_VERSION
    assert SqliteConnectionsRepo(again, Limits()).get(rec.id) == rec
    assert SqliteSettingsRepo(again).get().font_size == 18
    again.close()
