"""Bearer tokens: random, hash-only storage, 30-day sliding expiry, revocation."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from portkey.security.tokens import RENEW_EVERY, SqliteTokenStore
from portkey.store.db import Database

DAY = 24 * 3600.0
TTL = 30 * DAY


def test_issue_returns_random_urlsafe_tokens(tokens: SqliteTokenStore) -> None:
    a, b = tokens.issue(), tokens.issue()
    assert a.token != b.token
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", a.token)


def test_issue_expiry_is_thirty_days(tokens: SqliteTokenStore, wall: Any) -> None:
    issued = tokens.issue()
    assert issued.expires_at == datetime.fromtimestamp(int(wall.now) + TTL, UTC)
    assert issued.expires_at.tzinfo is not None


def test_only_the_hash_is_stored(tokens: SqliteTokenStore, db: Database) -> None:
    issued = tokens.issue()
    rows = db.query("SELECT * FROM tokens")
    assert len(rows) == 1
    assert rows[0]["token_hash"] == hashlib.sha256(issued.token.encode()).hexdigest()
    dump = "\n".join(db.conn.iterdump())
    assert issued.token not in dump


def test_token_repr_hides_the_value(tokens: SqliteTokenStore) -> None:
    issued = tokens.issue()
    assert issued.token not in repr(issued)


def test_verify(tokens: SqliteTokenStore) -> None:
    issued = tokens.issue()
    assert tokens.verify(issued.token)
    assert not tokens.verify(issued.token[:-1] + ("A" if issued.token[-1] != "A" else "B"))


@pytest.mark.parametrize(
    "bad",
    ["", "x", "a" * 10_000, "token with spaces", "é" * 43, "\x00" * 43, "Bearer abc", None, 42],
)
def test_verify_never_raises_for_garbage(tokens: SqliteTokenStore, bad: Any) -> None:
    tokens.issue()
    assert tokens.verify(bad) is False


def test_expired_token_is_refused_and_purged(
    tokens: SqliteTokenStore, wall: Any, db: Database
) -> None:
    issued = tokens.issue()
    wall.advance(TTL + 1)
    assert not tokens.verify(issued.token)
    assert db.query("SELECT * FROM tokens") == []


def test_expiry_boundary(tokens: SqliteTokenStore, wall: Any) -> None:
    issued = tokens.issue()
    wall.advance(TTL - 1)
    assert tokens.verify(issued.token)
    # verify above slid the expiry forward, so a fresh token is needed for the boundary.
    other = tokens.issue()
    wall.advance(TTL)
    assert not tokens.verify(other.token)


def test_sliding_renewal_keeps_an_active_token_alive(tokens: SqliteTokenStore, wall: Any) -> None:
    issued = tokens.issue()
    for _ in range(4):  # 4 x 29 days = 116 days, used every 29 days
        wall.advance(29 * DAY)
        assert tokens.verify(issued.token)
    expires = tokens.expires_at(issued.token)
    assert expires == datetime.fromtimestamp(int(wall.now + TTL), UTC)


def test_unused_token_lapses_after_thirty_days(tokens: SqliteTokenStore, wall: Any) -> None:
    issued = tokens.issue()
    wall.advance(29 * DAY)
    assert tokens.verify(issued.token)
    wall.advance(30 * DAY + 1)
    assert not tokens.verify(issued.token)


def test_renewal_writes_at_most_once_a_minute(
    tokens: SqliteTokenStore, wall: Any, db: Database
) -> None:
    issued = tokens.issue()
    before = tokens.expires_at(issued.token)
    wall.advance(RENEW_EVERY - 1)
    assert tokens.verify(issued.token)
    assert tokens.expires_at(issued.token) == before
    wall.advance(1)
    assert tokens.verify(issued.token)
    assert tokens.expires_at(issued.token) == datetime.fromtimestamp(int(wall.now + TTL), UTC)


def test_revoke(tokens: SqliteTokenStore) -> None:
    a, b = tokens.issue(), tokens.issue()
    tokens.revoke(a.token)
    assert not tokens.verify(a.token)
    assert tokens.verify(b.token)
    tokens.revoke(a.token)  # idempotent
    tokens.revoke("not a token")  # never raises


def test_revoke_all_except(tokens: SqliteTokenStore) -> None:
    keep, *others = (tokens.issue() for _ in range(4))
    assert tokens.revoke_all_except(keep.token) == 3
    assert tokens.verify(keep.token)
    assert not any(tokens.verify(t.token) for t in others)


def test_revoke_all_except_garbage_revokes_everything(tokens: SqliteTokenStore) -> None:
    a = tokens.issue()
    assert tokens.revoke_all_except("") == 1
    assert not tokens.verify(a.token)


def test_issue_purges_expired_rows(tokens: SqliteTokenStore, wall: Any, db: Database) -> None:
    tokens.issue()
    wall.advance(TTL + 1)
    tokens.issue()
    assert len(db.query("SELECT * FROM tokens")) == 1


def test_tokens_survive_a_restart(db: Database, wall: Any) -> None:
    first = SqliteTokenStore(db, TTL, clock=wall).issue()
    assert SqliteTokenStore(db, TTL, clock=wall).verify(first.token)
