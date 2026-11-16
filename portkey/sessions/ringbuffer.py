"""Output ring buffer addressed by absolute byte offset (package 5).

Keeps the newest ``capacity`` bytes (256 KB) of a session's output. ``total`` counts every byte
ever written; the oldest byte still held is at ``start == max(0, total - capacity)``.
``read_from(since)`` returns ``(offset, data)``: ``offset == since`` when ``since >= start``
(no gap), otherwise ``offset == start`` (the client must reset). ``since=None`` means "from
``start``". ``since > total`` is clamped to ``total``. ``limit`` caps how many bytes come back
(the output batcher reads at most one frame at a time).

The buffer is a ``bytearray`` trimmed from the front; CPython makes deleting a prefix of a
``bytearray`` cheap (it moves the start pointer), so a write costs about its own length.
Terminal output is never logged.
"""

from __future__ import annotations


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._data = bytearray()
        self._total = 0

    @property
    def total(self) -> int:
        """Bytes written since the session started (the offset of the next byte)."""
        return self._total

    @property
    def start(self) -> int:
        """Absolute offset of the oldest byte still held."""
        return self._total - len(self._data)

    def write(self, data: bytes) -> None:
        if not data:
            return
        self._total += len(data)
        if len(data) >= self.capacity:
            self._data[:] = data[-self.capacity :]
            return
        self._data += data
        excess = len(self._data) - self.capacity
        if excess > 0:
            del self._data[:excess]

    def holds(self, offset: int) -> bool:
        """True when ``offset`` can be read without a gap (``start <= offset <= total``)."""
        return self.start <= offset <= self._total

    def read_from(self, since: int | None, limit: int | None = None) -> tuple[int, bytes]:
        start = self.start
        offset = start if since is None or since < start else min(since, self._total)
        begin = offset - start
        end = len(self._data) if limit is None else min(len(self._data), begin + limit)
        return offset, bytes(self._data[begin:end])
