"""Text normalisation for every key and known_hosts read (package 3).

``read_text_normalized`` strips a UTF-8 BOM, decodes UTF-16 when it sees an ``FF FE`` or
``FE FF`` BOM (PowerShell 5 ``>`` writes that), and turns CRLF (and lone CR) into LF.
Anything Port-Key writes is LF-terminated UTF-8.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"


def decode_text(data: bytes) -> str:
    """Decode bytes from a key or known_hosts file as described above. Invalid UTF-8 raises
    ``UnicodeDecodeError``."""
    if data.startswith(_UTF8_BOM):
        text = data[len(_UTF8_BOM) :].decode("utf-8")
    elif data.startswith(_UTF16_LE_BOM):
        text = data[len(_UTF16_LE_BOM) :].decode("utf-16-le")
    elif data.startswith(_UTF16_BE_BOM):
        text = data[len(_UTF16_BE_BOM) :].decode("utf-16-be")
    else:
        text = data.decode("utf-8")
    # A BOM can survive a round trip through another tool; drop a stray one at the start.
    text = text.removeprefix("﻿")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_bytes_limited(path: Path, max_bytes: int) -> bytes:
    """Read a regular file of at most ``max_bytes``. Opening never blocks (a FIFO or device is
    refused with ``ValueError``); a larger file raises ``ValueError``; I/O problems raise
    ``OSError``."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > max_bytes:
            raise ValueError("file too large")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise ValueError("file too large")
    return data


def read_text_normalized(path: Path, max_bytes: int = 1024 * 1024) -> str:
    """Read and ``decode_text`` a file. Files larger than ``max_bytes`` raise ``ValueError``."""
    return decode_text(read_bytes_limited(path, max_bytes))
