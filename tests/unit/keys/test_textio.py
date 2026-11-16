"""textio: one reader for key and known_hosts files written by Windows tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from portkey.keys.textio import decode_text, read_text_normalized

TEXT = "web-prod ssh-ed25519 AAAA\n192.168.10.20 ssh-rsa BBBB\n"


@pytest.mark.parametrize(
    "data",
    [
        TEXT.encode(),
        TEXT.replace("\n", "\r\n").encode(),
        b"\xef\xbb\xbf" + TEXT.encode(),
        b"\xef\xbb\xbf" + TEXT.replace("\n", "\r\n").encode(),
        b"\xff\xfe" + TEXT.replace("\n", "\r\n").encode("utf-16-le"),
        b"\xfe\xff" + TEXT.replace("\n", "\r\n").encode("utf-16-be"),
        b"\xff\xfe" + TEXT.encode("utf-16-le"),
    ],
    ids=["lf", "crlf", "bom", "bom-crlf", "utf16le-powershell", "utf16be", "utf16le-lf"],
)
def test_decode_variants_give_lf_text(data: bytes) -> None:
    assert decode_text(data) == TEXT


def test_lone_cr_becomes_lf() -> None:
    assert decode_text(b"a\rb\r\nc") == "a\nb\nc"


def test_utf16_bom_inside_utf8_is_dropped() -> None:
    assert decode_text("﻿hello".encode()) == "hello"


def test_invalid_utf8_raises() -> None:
    with pytest.raises(UnicodeDecodeError):
        decode_text(b"\xff\x00\xc3(")


def test_empty() -> None:
    assert decode_text(b"") == ""
    assert decode_text(b"\xff\xfe") == ""


def test_read_file(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    path.write_bytes(b"\xff\xfe" + TEXT.replace("\n", "\r\n").encode("utf-16-le"))
    os.chmod(path, 0o777)  # Windows bind mounts look like this; it must not matter
    assert read_text_normalized(path) == TEXT


def test_read_too_large(tmp_path: Path) -> None:
    path = tmp_path / "big"
    path.write_bytes(b"x" * 101)
    with pytest.raises(ValueError, match="too large"):
        read_text_normalized(path, max_bytes=100)
    assert read_text_normalized(path, max_bytes=101) == "x" * 101


def test_read_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "pipe"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular"):
        read_text_normalized(path)


def test_read_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises((ValueError, OSError)):
        read_text_normalized(tmp_path)


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_text_normalized(tmp_path / "nope")
