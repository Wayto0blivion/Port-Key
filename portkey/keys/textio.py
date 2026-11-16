"""Text normalisation for every key and known_hosts read (package 3).

``read_text_normalized`` strips a UTF-8 BOM, decodes UTF-16 when it sees an ``FF FE`` or
``FE FF`` BOM (PowerShell 5 ``>`` writes that), and turns CRLF (and lone CR) into LF.
Anything Port-Key writes is LF-terminated UTF-8.
"""

from __future__ import annotations

from pathlib import Path


def decode_text(data: bytes) -> str:
    """Decode bytes from a key or known_hosts file as described above. Invalid UTF-8 raises
    ``UnicodeDecodeError``."""
    raise NotImplementedError("package 3")


def read_text_normalized(path: Path, max_bytes: int = 1024 * 1024) -> str:
    """Read and ``decode_text`` a file. Files larger than ``max_bytes`` raise ``ValueError``."""
    raise NotImplementedError("package 3")
