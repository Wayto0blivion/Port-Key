"""Audit event log (package 2). Implements ``interfaces.EventLog``.

The ``events_cap`` trigger keeps the newest 5,000 rows. ``detail`` must never contain secrets,
terminal data or WebSocket payloads: host, user, fingerprints and outcome codes only.

As a backstop, detail keys whose names suggest a secret are dropped before the row is written,
and values that are not JSON types are stored as their ``str()``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, get_args

from portkey.errors import Invalid
from portkey.models import EventKind, EventRecord
from portkey.store.db import Database, from_iso, now_iso

log = logging.getLogger(__name__)

KINDS = frozenset(get_args(EventKind))
_SECRET_KEY = re.compile(r"pass|secret|token|private|otp|answer|value|data|input|output", re.I)


def _safe_detail(detail: dict[str, Any]) -> dict[str, Any]:
    dropped = sorted(k for k in detail if _SECRET_KEY.search(k))
    if dropped:
        log.warning("event detail keys dropped: %s", ", ".join(dropped))
    return {k: v for k, v in detail.items() if k not in dropped}


class SqliteEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, kind: EventKind, connection_id: str | None = None, **detail: Any) -> None:
        if kind not in KINDS:
            raise Invalid(f"Unknown event kind {kind!r}.")
        text = json.dumps(_safe_detail(detail), default=str, sort_keys=True)
        self.db.execute(
            "INSERT INTO events (ts, kind, connection_id, detail) VALUES (?, ?, ?, ?)",
            (now_iso(), kind, connection_id, text),
        )

    def recent(self, limit: int = 100) -> list[EventRecord]:
        rows = self.db.query(
            "SELECT id, ts, kind, connection_id, detail FROM events ORDER BY id DESC LIMIT ?",
            (max(0, int(limit)),),
        )
        return [
            EventRecord(
                id=int(r[0]),
                ts=from_iso(r[1]),
                kind=r[2],
                connection_id=r[3],
                detail=json.loads(r[4]),
            )
            for r in rows
        ]
