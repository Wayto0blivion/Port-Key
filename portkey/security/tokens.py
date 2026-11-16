"""Bearer tokens (package 1). Implements ``interfaces.TokenStore``.

Tokens are ``secrets.token_urlsafe(32)``; only ``sha256(token)`` hex is stored in ``tokens``.
Expiry is 30 days, sliding: each successful ``verify`` moves ``expires_at`` forward (write at
most once a minute per token). Expired rows are purged opportunistically.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from portkey.models import IssuedToken
from portkey.store.db import Database


class SqliteTokenStore:
    def __init__(self, db: Database, ttl: float, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.ttl = ttl
        self.clock = clock

    def issue(self) -> IssuedToken:
        raise NotImplementedError("package 1")

    def verify(self, token: str) -> bool:
        raise NotImplementedError("package 1")

    def revoke(self, token: str) -> None:
        raise NotImplementedError("package 1")

    def revoke_all_except(self, token: str) -> int:
        raise NotImplementedError("package 1")
