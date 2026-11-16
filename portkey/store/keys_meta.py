"""Key metadata repository (package 2). Implements ``interfaces.KeysMetaRepo``.

``keys_meta`` holds only labels, the hidden flag and provenance; keys themselves are discovered
on disk by ``keys.discovery`` on every list call.

``set_label`` and ``set_hidden`` need the key to have been seen (``upsert_seen`` runs on every
scan), otherwise they raise ``NotFound(key_unknown)``. Hiding the key that Settings names as the
default also clears ``default_key_fp`` in the same transaction (docs/api.md, "Keys": "if it was
the default key the default is cleared").
"""

from __future__ import annotations

import json

from portkey.errors import ErrorCode, Invalid, NotFound
from portkey.models import KeysMetaRecord, KeySource
from portkey.store.db import Database, from_iso, now_iso

LABEL_MAX = 64


class SqliteKeysMetaRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def all(self) -> dict[str, KeysMetaRecord]:
        rows = self.db.query(
            "SELECT fp, label, last_path, source, hidden, created_at FROM keys_meta"
        )
        return {
            r[0]: KeysMetaRecord(
                fp=r[0],
                label=r[1],
                last_path=r[2],
                source=r[3],
                hidden=bool(r[4]),
                created_at=from_iso(r[5]),
            )
            for r in rows
        }

    def upsert_seen(self, fp: str, last_path: str, source: KeySource) -> None:
        if source not in ("ssh_dir", "generated"):
            raise Invalid("Key source must be ssh_dir or generated.")
        self.db.execute(
            "INSERT INTO keys_meta (fp, last_path, source, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (fp) DO UPDATE SET last_path = excluded.last_path,"
            " source = excluded.source"
            " WHERE last_path <> excluded.last_path OR source <> excluded.source",
            (fp, last_path, source, now_iso()),
        )

    def set_label(self, fp: str, label: str | None) -> None:
        clean = (label or "").strip() or None
        if clean is not None and len(clean) > LABEL_MAX:
            raise Invalid(f"Labels can be at most {LABEL_MAX} characters.")
        with self.db.tx() as c:
            if c.execute("UPDATE keys_meta SET label = ? WHERE fp = ?", (clean, fp)).rowcount == 0:
                raise _unknown()

    def set_hidden(self, fp: str, hidden: bool) -> None:
        with self.db.tx() as c:
            cur = c.execute("UPDATE keys_meta SET hidden = ? WHERE fp = ?", (int(hidden), fp))
            if cur.rowcount == 0:
                raise _unknown()
            if hidden:
                c.execute(
                    "DELETE FROM settings WHERE key = 'default_key_fp' AND value = ?",
                    (json.dumps(fp),),
                )


def _unknown() -> NotFound:
    return NotFound("Port-Key hasn't seen that key.", code=ErrorCode.KEY_UNKNOWN)
