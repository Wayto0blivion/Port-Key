"""Key metadata repository (package 2). Implements ``interfaces.KeysMetaRepo``.

``keys_meta`` holds only labels, the hidden flag and provenance; keys themselves are discovered
on disk by ``keys.discovery`` on every list call.
"""

from __future__ import annotations

from portkey.models import KeysMetaRecord, KeySource
from portkey.store.db import Database


class SqliteKeysMetaRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def all(self) -> dict[str, KeysMetaRecord]:
        raise NotImplementedError("package 2")

    def upsert_seen(self, fp: str, last_path: str, source: KeySource) -> None:
        raise NotImplementedError("package 2")

    def set_label(self, fp: str, label: str | None) -> None:
        raise NotImplementedError("package 2")

    def set_hidden(self, fp: str, hidden: bool) -> None:
        raise NotImplementedError("package 2")
