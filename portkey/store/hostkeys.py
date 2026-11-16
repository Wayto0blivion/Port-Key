"""App-trusted host keys and dismissed suggestions (package 2).

Implements ``interfaces.HostKeyStore`` and ``interfaces.DismissedSuggestions``. Hosts are stored
lower-case. The owner's known_hosts file is never written; that is read by
``keys.known_hosts``.

Public keys are stored as ``"<type> <base64>"`` with any comment dropped, so the same key always
matches the ``(host, port, public_key)`` unique index. The SHA256 fingerprint is computed here
from the key blob, in OpenSSH's format (``SHA256:`` plus unpadded base64).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import sqlite3

from portkey.errors import Invalid, NotFound
from portkey.models import HostKeyRecord
from portkey.store.db import Database, from_iso, now_iso

_COLUMNS = "id, host, port, key_type, public_key, fp_sha256, first_seen_at, last_seen_at"


def normalize_host(host: str) -> str:
    return host.strip().lower()


def parse_public_key(text: str) -> tuple[str, str, str]:
    """``(key_type, "type base64", fingerprint)`` from OpenSSH public-key text. Raises
    ``Invalid`` when it is not one."""
    parts = text.strip().split()
    if len(parts) < 2:
        raise Invalid("That is not an OpenSSH public key.")
    key_type, b64 = parts[0], parts[1]
    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid("That is not an OpenSSH public key.") from None
    if not blob:
        raise Invalid("That is not an OpenSSH public key.")
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return key_type, f"{key_type} {b64}", f"SHA256:{digest}"


def _record(row: sqlite3.Row) -> HostKeyRecord:
    return HostKeyRecord(
        id=int(row["id"]),
        host=row["host"],
        port=int(row["port"]),
        key_type=row["key_type"],
        public_key=row["public_key"],
        fp_sha256=row["fp_sha256"],
        first_seen_at=from_iso(row["first_seen_at"]),
        last_seen_at=from_iso(row["last_seen_at"]),
    )


def _check_port(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise Invalid("Port must be a number from 1 to 65535.")


class SqliteHostKeyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[HostKeyRecord]:
        rows = self.db.query(f"SELECT {_COLUMNS} FROM host_keys ORDER BY host, port, id")  # noqa: S608
        return [_record(r) for r in rows]

    def get(self, key_id: int) -> HostKeyRecord:
        row = self.db.query_one(f"SELECT {_COLUMNS} FROM host_keys WHERE id = ?", (key_id,))  # noqa: S608
        if row is None:
            raise NotFound("That host key isn't stored in Port-Key.")
        return _record(row)

    def for_host(self, host: str, port: int) -> list[HostKeyRecord]:
        rows = self.db.query(
            f"SELECT {_COLUMNS} FROM host_keys WHERE host = ? AND port = ? ORDER BY id",  # noqa: S608
            (normalize_host(host), port),
        )
        return [_record(r) for r in rows]

    def add(self, host: str, port: int, public_key: str) -> HostKeyRecord:
        with self.db.tx() as c:
            return self._add(c, host, port, public_key)

    def replace(self, host: str, port: int, public_key: str) -> HostKeyRecord:
        parse_public_key(public_key)  # fail before deleting anything
        with self.db.tx() as c:
            c.execute(
                "DELETE FROM host_keys WHERE host = ? AND port = ?", (normalize_host(host), port)
            )
            return self._add(c, host, port, public_key)

    def touch(self, key_id: int) -> None:
        self.db.execute("UPDATE host_keys SET last_seen_at = ? WHERE id = ?", (now_iso(), key_id))

    def delete(self, key_id: int) -> HostKeyRecord:
        with self.db.tx():
            record = self.get(key_id)
            self.db.execute("DELETE FROM host_keys WHERE id = ?", (key_id,))
        return record

    def _add(self, c: sqlite3.Connection, host: str, port: int, public_key: str) -> HostKeyRecord:
        _check_port(port)
        name = normalize_host(host)
        if not name:
            raise Invalid("A host key needs a host name.")
        key_type, text, fp = parse_public_key(public_key)
        ts = now_iso()
        c.execute(
            "INSERT INTO host_keys (host, port, key_type, public_key, fp_sha256, first_seen_at,"
            " last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (host, port, public_key)"
            " DO UPDATE SET last_seen_at = excluded.last_seen_at",
            (name, port, key_type, text, fp, ts, ts),
        )
        row = c.execute(
            f"SELECT {_COLUMNS} FROM host_keys WHERE host = ? AND port = ? AND public_key = ?",  # noqa: S608
            (name, port, text),
        ).fetchone()
        return _record(row)


class SqliteDismissedSuggestions:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> set[tuple[str, int]]:
        rows = self.db.query("SELECT host, port FROM dismissed_suggestions")
        return {(r[0], int(r[1])) for r in rows}

    def dismiss(self, host: str, port: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO dismissed_suggestions (host, port, dismissed_at)"
            " VALUES (?, ?, ?)",
            (normalize_host(host), port, now_iso()),
        )

    def restore(self, host: str, port: int) -> None:
        self.db.execute(
            "DELETE FROM dismissed_suggestions WHERE host = ? AND port = ?",
            (normalize_host(host), port),
        )
