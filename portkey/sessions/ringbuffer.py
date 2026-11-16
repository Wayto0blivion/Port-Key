"""Output ring buffer addressed by absolute byte offset (package 5).

Keeps the newest ``capacity`` bytes (256 KB) of a session's output. ``total`` counts every byte
ever written; the oldest byte still held is at ``start == max(0, total - capacity)``.
``read_from(since)`` returns ``(offset, data)``: ``offset == since`` when ``since >= start``
(no gap), otherwise ``offset == start`` (the client must reset). ``since=None`` means "from
``start``". ``since > total`` is clamped to ``total``.
"""

from __future__ import annotations


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity

    @property
    def total(self) -> int:
        raise NotImplementedError("package 5")

    @property
    def start(self) -> int:
        raise NotImplementedError("package 5")

    def write(self, data: bytes) -> None:
        raise NotImplementedError("package 5")

    def read_from(self, since: int | None) -> tuple[int, bytes]:
        raise NotImplementedError("package 5")
