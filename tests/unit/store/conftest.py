"""Fixtures for the storage tests (package 2): a migrated temp database and every repository,
plus a fake key provider for the routes that check ``key_fp``."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from portkey.config import Limits
from portkey.errors import ErrorCode, NotFound
from portkey.models import ConnectionDraft, ConnectionRecord, KeyRecord
from portkey.services import Services
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.groups import SqliteGroupsRepo
from portkey.store.hostkeys import SqliteDismissedSuggestions, SqliteHostKeyStore
from portkey.store.keys_meta import SqliteKeysMetaRepo
from portkey.store.settings import SqliteSettingsRepo
from portkey.store.tags import SqliteTagsRepo

FP_A = "SHA256:" + "A" * 43
FP_B = "SHA256:" + "B" * 42 + "+"
FP_HIDDEN = "SHA256:" + "C" * 42 + "/"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "portkey.db")
    d.migrate()
    yield d
    d.close()


@pytest.fixture
def conns(db: Database) -> SqliteConnectionsRepo:
    return SqliteConnectionsRepo(db, Limits())


@pytest.fixture
def groups(db: Database) -> SqliteGroupsRepo:
    return SqliteGroupsRepo(db, Limits())


@pytest.fixture
def tags(db: Database) -> SqliteTagsRepo:
    return SqliteTagsRepo(db)


@pytest.fixture
def host_keys(db: Database) -> SqliteHostKeyStore:
    return SqliteHostKeyStore(db)


@pytest.fixture
def dismissed(db: Database) -> SqliteDismissedSuggestions:
    return SqliteDismissedSuggestions(db)


@pytest.fixture
def settings(db: Database) -> SqliteSettingsRepo:
    return SqliteSettingsRepo(db)


@pytest.fixture
def events(db: Database) -> SqliteEventLog:
    return SqliteEventLog(db)


@pytest.fixture
def keys_meta(db: Database) -> SqliteKeysMetaRepo:
    return SqliteKeysMetaRepo(db)


Make = Callable[..., ConnectionRecord]


@pytest.fixture
def make(conns: SqliteConnectionsRepo) -> Make:
    """``make(name, **fields)`` creates a connection with test defaults (fictional hosts)."""

    def _make(name: str, **fields: Any) -> ConnectionRecord:
        return _create(conns, name, **fields)

    return _make


def _create(repo: SqliteConnectionsRepo, name: str, **fields: Any) -> ConnectionRecord:
    values: dict[str, Any] = {
        "host": "192.168.10.42",
        "username": "john",
        "auth_method": "key",
    }
    values.update(fields)
    if "tags" in values:
        values["tags"] = tuple(values["tags"])
    return repo.create(ConnectionDraft(name=name, **values))


def key_record(fp: str, name: str, hidden: bool = False) -> KeyRecord:
    return KeyRecord(
        fp=fp,
        name=name,
        type="ed25519",
        bits=256,
        comment="john@WORKPC",
        path=Path("/ssh") / name,
        display_path=f"C:\\Users\\john\\.ssh\\{name}",
        source="ssh_dir",
        encrypted=False,
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST john@WORKPC",
        hidden=hidden,
    )


class FakeKeys:
    """Just enough of ``KeyProvider`` for ``require_known_key``."""

    a = FP_A
    b = FP_B
    hidden = FP_HIDDEN

    def __init__(self) -> None:
        self.keys = {
            FP_A: key_record(FP_A, "id_ed25519"),
            FP_B: key_record(FP_B, "id_ed25519_portkey"),
            FP_HIDDEN: key_record(FP_HIDDEN, "old_key", hidden=True),
        }

    def get(self, fp: str) -> KeyRecord:
        if fp not in self.keys:
            raise NotFound("unknown key", code=ErrorCode.KEY_UNKNOWN)
        return self.keys[fp]


@pytest.fixture
def fake_keys() -> FakeKeys:
    return FakeKeys()


@pytest.fixture
def api(authed_client: httpx.AsyncClient, services: Services, fake_keys: FakeKeys) -> Any:
    """``authed_client`` with ``services.keys`` replaced by ``FakeKeys``."""
    services.keys = fake_keys  # type: ignore[assignment]
    return authed_client
