"""App-trusted host keys and dismissed suggestions (package 2).

Implements ``interfaces.HostKeyStore`` and ``interfaces.DismissedSuggestions``. Hosts are stored
lower-case. The owner's known_hosts file is never written; that is read by
``keys.known_hosts``.
"""

from __future__ import annotations

from portkey.models import HostKeyRecord
from portkey.store.db import Database


class SqliteHostKeyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[HostKeyRecord]:
        raise NotImplementedError("package 2")

    def get(self, key_id: int) -> HostKeyRecord:
        raise NotImplementedError("package 2")

    def for_host(self, host: str, port: int) -> list[HostKeyRecord]:
        raise NotImplementedError("package 2")

    def add(self, host: str, port: int, public_key: str) -> HostKeyRecord:
        raise NotImplementedError("package 2")

    def replace(self, host: str, port: int, public_key: str) -> HostKeyRecord:
        raise NotImplementedError("package 2")

    def touch(self, key_id: int) -> None:
        raise NotImplementedError("package 2")

    def delete(self, key_id: int) -> HostKeyRecord:
        raise NotImplementedError("package 2")


class SqliteDismissedSuggestions:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> set[tuple[str, int]]:
        raise NotImplementedError("package 2")

    def dismiss(self, host: str, port: int) -> None:
        raise NotImplementedError("package 2")

    def restore(self, host: str, port: int) -> None:
        raise NotImplementedError("package 2")
