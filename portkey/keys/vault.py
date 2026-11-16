"""In-memory key vault (package 3). Implements ``interfaces.KeyVault``.

Decrypted keys live only here, each with its own expiry, never on disk. Thread-safe. ``clock``
is injectable for tests (monotonic seconds).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncssh


class MemoryKeyVault:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock

    def get(self, fp: str) -> asyncssh.SSHKey | None:
        raise NotImplementedError("package 3")

    def put(self, fp: str, key: asyncssh.SSHKey, ttl: float | None) -> None:
        raise NotImplementedError("package 3")

    def lock_all(self) -> int:
        raise NotImplementedError("package 3")

    def unlocked(self) -> list[str]:
        raise NotImplementedError("package 3")
