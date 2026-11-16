"""Where a full replay may start without garbling the terminal (package 5).

When the ring buffer has wrapped, its oldest byte can sit in the middle of a UTF-8 sequence or an
escape sequence: replayed as is, the terminal would print a replacement character or the tail of
a colour code (``[31m``) or of a title (OSC) as text. A full replay (``reset: true``) therefore
skips forward, within the first ``SCAN_BYTES``, to the first of:

1. the byte after a line feed: LF never occurs inside a UTF-8 sequence and in practice ends any
   escape sequence a shell or editor writes;
2. an ESC (0x1b): it starts a new sequence, and terminals abandon a half-read one on ESC;
3. the first byte that is not a UTF-8 continuation byte (0x80 to 0xBF).

The bytes skipped are simply not sent: ``ready.offset`` is the offset of the first byte sent, so
the client's byte count stays exact. A replay of a buffer that never wrapped starts at offset 0,
the true start of the output, and is never trimmed. Resuming with ``since`` inside the buffer is
never trimmed either: the client's terminal is already in the right state for that byte.
"""

from __future__ import annotations

SCAN_BYTES = 4096
_LF = 0x0A
_ESC = 0x1B


def _is_continuation(byte: int) -> bool:
    return 0x80 <= byte <= 0xBF


def safe_skip(head: bytes) -> int:
    """How many leading bytes of ``head`` (the start of a truncated replay) to skip."""
    window = head[:SCAN_BYTES]
    newline = window.find(_LF)
    if newline != -1:
        return newline + 1
    escape = window.find(_ESC)
    if escape != -1:
        return escape
    skip = 0
    while skip < min(len(window), 3) and _is_continuation(window[skip]):
        skip += 1  # a UTF-8 sequence has at most 3 continuation bytes
    return skip
