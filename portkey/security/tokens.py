"""Bearer tokens (package 1). Implements ``interfaces.TokenStore``.

Tokens are ``secrets.token_urlsafe(32)``; only ``sha256(token)`` hex is stored in ``tokens``.
Expiry is 30 days, sliding: each successful ``verify`` moves ``expires_at`` forward (write at
most once a minute per token). On top of that a token has an absolute lifetime of 90 days from
``created_at``: sliding never moves ``expires_at`` past it, so a stolen token that keeps being
used still dies. Expired rows are purged opportunistically.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime

from portkey.models import IssuedToken
from portkey.store.db import Database, from_iso, to_iso

TOKEN_BYTES = 32
# Sliding renewal writes at most this often per token, so a busy page does not write on every
# request.
RENEW_EVERY = 60.0
# Absolute lifetime (config.Limits.token_max_age); the owner signs in again after this.
MAX_AGE = 90 * 24 * 3600.0
# token_urlsafe(32) is 43 characters; anything far outside the alphabet or length is rejected
# before it reaches SQLite.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{16,256}")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def well_formed(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_SHAPE.fullmatch(token) is not None


def _iso(ts: float) -> str:
    return to_iso(datetime.fromtimestamp(ts, UTC))


class SqliteTokenStore:
    def __init__(
        self,
        db: Database,
        ttl: float,
        clock: Callable[[], float] = time.time,
        *,
        max_age: float = MAX_AGE,
    ) -> None:
        self.db = db
        self.ttl = ttl
        self.max_age = max_age
        self.clock = clock

    def issue(self) -> IssuedToken:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = int(self.clock())
        expires = now + int(min(self.ttl, self.max_age))
        with self.db.tx() as c:
            c.execute(
                "DELETE FROM tokens WHERE expires_at <= ? OR created_at <= ?",
                (_iso(now), _iso(now - self.max_age)),
            )
            c.execute(
                "INSERT INTO tokens (token_hash, created_at, expires_at, last_used_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash(token), _iso(now), _iso(expires), _iso(now)),
            )
        return IssuedToken(token=token, expires_at=datetime.fromtimestamp(expires, UTC))

    def verify(self, token: str) -> bool:
        if not well_formed(token):
            return False
        digest = token_hash(token)
        now = self.clock()
        with self.db.tx() as c:
            row = c.execute(
                "SELECT created_at, expires_at, last_used_at FROM tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return False
            # Checked separately from expires_at, so rows written before the absolute
            # lifetime existed are capped too.
            dies_at = from_iso(row["created_at"]).timestamp() + self.max_age
            if from_iso(row["expires_at"]).timestamp() <= now or dies_at <= now:
                c.execute("DELETE FROM tokens WHERE token_hash = ?", (digest,))
                return False
            if now - from_iso(row["last_used_at"]).timestamp() >= RENEW_EVERY:
                c.execute(
                    "UPDATE tokens SET expires_at = ?, last_used_at = ? WHERE token_hash = ?",
                    (_iso(min(now + self.ttl, dies_at)), _iso(now), digest),
                )
        return True

    def is_valid(self, token: str) -> bool:
        """True while the token is stored and unexpired. Unlike ``verify`` it never extends the
        expiry: open WebSockets re-check their token with it, and an idle open socket must not
        keep a token alive."""
        if not well_formed(token):
            return False
        row = self.db.query_one(
            "SELECT created_at, expires_at FROM tokens WHERE token_hash = ?", (token_hash(token),)
        )
        if row is None:
            return False
        now = self.clock()
        dies_at = from_iso(row["created_at"]).timestamp() + self.max_age
        return from_iso(row["expires_at"]).timestamp() > now and dies_at > now

    def expires_at(self, token: str) -> datetime | None:
        """Current expiry of a stored token (tests and diagnostics); None if unknown."""
        if not well_formed(token):
            return None
        row = self.db.query_one(
            "SELECT expires_at FROM tokens WHERE token_hash = ?", (token_hash(token),)
        )
        return from_iso(row["expires_at"]) if row else None

    def revoke(self, token: str) -> None:
        if not well_formed(token):
            return
        self.db.execute("DELETE FROM tokens WHERE token_hash = ?", (token_hash(token),))

    def revoke_all_except(self, token: str) -> int:
        keep = token_hash(token) if well_formed(token) else ""
        cur = self.db.execute("DELETE FROM tokens WHERE token_hash <> ?", (keep,))
        return int(cur.rowcount)
