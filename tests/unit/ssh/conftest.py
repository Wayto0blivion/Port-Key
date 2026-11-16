"""Fixtures for package 4's unit tests; the fakes they use are in ``fakes.py``.

- ``db``: a migrated SQLite database in a temp dir.
- ``store``: a real ``SqliteHostKeyStore`` on it; ``events``: its ``SqliteEventLog``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.hostkeys import SqliteHostKeyStore


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "portkey.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> SqliteHostKeyStore:
    return SqliteHostKeyStore(db)


@pytest.fixture
def events(db: Database) -> SqliteEventLog:
    return SqliteEventLog(db)
