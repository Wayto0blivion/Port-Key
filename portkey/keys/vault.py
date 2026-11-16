"""In-memory key vault (package 3). Implements ``interfaces.KeyVault``.

Decrypted keys live only here, each with its own expiry, never on disk. Thread-safe. ``clock``
is injectable for tests (monotonic seconds).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncssh


class MemoryKeyVault:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        # fp -> (key, expires_at or None for "until lock_all")
        self._held: dict[str, tuple[asyncssh.SSHKey, float | None]] = {}

    def __repr__(self) -> str:
        # Never show the keys themselves.
        return f"<MemoryKeyVault holding {len(self.unlocked())} key(s)>"

    def get(self, fp: str) -> asyncssh.SSHKey | None:
        with self._lock:
            self._purge()
            entry = self._held.get(fp)
            return entry[0] if entry else None

    def put(self, fp: str, key: asyncssh.SSHKey, ttl: float | None) -> None:
        with self._lock:
            expires = None if ttl is None else self.clock() + max(0.0, float(ttl))
            self._held[fp] = (key, expires)
            self._purge()

    def lock_all(self) -> int:
        with self._lock:
            self._purge()
            count = len(self._held)
            self._held.clear()
            return count

    def unlocked(self) -> list[str]:
        with self._lock:
            self._purge()
            return sorted(self._held)

    def _purge(self) -> None:
        """Drop expired keys. Caller holds the lock."""
        now = self.clock()
        for fp in [fp for fp, (_, exp) in self._held.items() if exp is not None and exp <= now]:
            del self._held[fp]
