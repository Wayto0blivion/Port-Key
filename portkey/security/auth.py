"""App password and login (package 1). Implements ``interfaces.AppAuth``.

One app password, hashed with ``hashlib.scrypt`` (salt and n/r/p stored in ``app_auth``),
compared in constant time. Failed logins get per-client exponential backoff (``RateLimited``
with ``retry_after``; the UI says "Too many attempts. Try again in 30 seconds."). Changing the
password revokes every token except the caller's.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from portkey.interfaces import TokenStore
from portkey.models import IssuedToken
from portkey.store.db import Database


class PasswordAuth:
    def __init__(
        self, db: Database, tokens: TokenStore, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.db = db
        self.tokens = tokens
        self.clock = clock

    def is_setup(self) -> bool:
        raise NotImplementedError("package 1")

    def setup(self, password: str) -> IssuedToken:
        raise NotImplementedError("package 1")

    def login(self, password: str, client: str) -> IssuedToken:
        raise NotImplementedError("package 1")

    def change_password(self, token: str, old: str, new: str) -> None:
        raise NotImplementedError("package 1")

    def authenticate(self, authorization: str | None) -> str:
        raise NotImplementedError("package 1")
