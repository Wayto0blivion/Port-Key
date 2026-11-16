"""Audit event log (package 2). Implements ``interfaces.EventLog``.

The ``events_cap`` trigger keeps the newest 5,000 rows. ``detail`` must never contain secrets,
terminal data or WebSocket payloads: host, user, fingerprints and outcome codes only.
"""

from __future__ import annotations

from typing import Any

from portkey.models import EventKind, EventRecord
from portkey.store.db import Database


class SqliteEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, kind: EventKind, connection_id: str | None = None, **detail: Any) -> None:
        raise NotImplementedError("package 2")

    def recent(self, limit: int = 100) -> list[EventRecord]:
        raise NotImplementedError("package 2")
