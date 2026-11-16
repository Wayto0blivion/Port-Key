"""RingBuffer: absolute offsets, wraparound, replay from ``since``, and the replay trim."""

from __future__ import annotations

import pytest

from portkey.sessions._replay import SCAN_BYTES, safe_skip
from portkey.sessions.ringbuffer import RingBuffer


def test_empty_buffer() -> None:
    buf = RingBuffer(8)
    assert (buf.total, buf.start) == (0, 0)
    assert buf.read_from(None) == (0, b"")
    assert buf.read_from(0) == (0, b"")
    assert buf.read_from(5) == (0, b"")  # beyond what was written: everything sent


def test_offsets_before_wrapping() -> None:
    buf = RingBuffer(8)
    buf.write(b"abc")
    buf.write(b"de")
    assert (buf.total, buf.start) == (5, 0)
    assert buf.read_from(None) == (0, b"abcde")
    assert buf.read_from(2) == (2, b"cde")
    assert buf.read_from(5) == (5, b"")


def test_wraparound_keeps_the_newest_bytes() -> None:
    buf = RingBuffer(8)
    for chunk in (b"0123", b"4567", b"89", b"AB"):
        buf.write(chunk)
    assert (buf.total, buf.start) == (12, 4)
    assert buf.read_from(None) == (4, b"456789AB")
    assert buf.read_from(4) == (4, b"456789AB")
    assert buf.read_from(10) == (10, b"AB")


def test_since_older_than_the_buffer_returns_the_oldest_byte() -> None:
    buf = RingBuffer(8)
    buf.write(b"0123456789")
    offset, data = buf.read_from(1)
    assert offset == buf.start == 2  # the caller sees offset != since: reset
    assert data == b"23456789"


def test_since_beyond_total_is_clamped() -> None:
    buf = RingBuffer(8)
    buf.write(b"abc")
    assert buf.read_from(99) == (3, b"")


def test_a_write_bigger_than_the_buffer() -> None:
    buf = RingBuffer(4)
    buf.write(b"xy")
    buf.write(b"0123456789")
    assert (buf.total, buf.start) == (12, 8)
    assert buf.read_from(None) == (8, b"6789")


def test_limit_caps_the_read() -> None:
    buf = RingBuffer(16)
    buf.write(b"0123456789")
    assert buf.read_from(2, limit=3) == (2, b"234")
    assert buf.read_from(8, limit=5) == (8, b"89")
    assert buf.read_from(None, limit=0) == (0, b"")


def test_holds() -> None:
    buf = RingBuffer(4)
    buf.write(b"012345")
    assert [buf.holds(i) for i in range(1, 8)] == [False, True, True, True, True, True, False]


def test_every_offset_matches_a_reference_stream() -> None:
    """Many uneven writes: every readable offset returns exactly the reference bytes."""
    buf = RingBuffer(97)
    stream = bytearray()
    for i in range(300):
        chunk = bytes((i * 7 + j) % 256 for j in range(i % 41))
        buf.write(chunk)
        stream += chunk
        assert buf.total == len(stream)
        assert buf.start == max(0, len(stream) - 97)
        for since in (buf.start, (buf.start + buf.total) // 2, buf.total):
            offset, data = buf.read_from(since)
            assert offset == since
            assert data == bytes(stream[since:])


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        RingBuffer(0)


# --- where a full replay starts (portkey.sessions._replay) ----------------------------------------


def test_skip_to_after_the_first_line_feed() -> None:
    assert safe_skip(b"1m tail of a line\r\nnext line") == len(b"1m tail of a line\r\n")


def test_skip_a_cut_escape_sequence_without_a_line_feed() -> None:
    head = b"31;1mtext\x1b[H\x1b[2Jscreen"
    assert head[safe_skip(head) :] == b"\x1b[H\x1b[2Jscreen"


def test_skip_utf8_continuation_bytes() -> None:
    text = "é€x".encode()  # c3 a9, e2 82 ac, 78
    assert safe_skip(text[1:]) == 1  # a9 is a continuation byte
    assert text[1:][safe_skip(text[1:]) :].decode() == "€x"
    assert safe_skip(text[4:]) == 1
    assert safe_skip(b"plain") == 0


def test_skip_never_looks_past_the_scan_window() -> None:
    head = b"a" * SCAN_BYTES + b"\n"
    assert safe_skip(head) == 0
