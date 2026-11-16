"""known_hosts line parsing and host matching, following OpenSSH's rules.

- A line is ``[@marker] patterns keytype base64 [comment]``; markers are ``@cert-authority``
  and ``@revoked``. Blank lines, comments and malformed lines are skipped.
- ``patterns`` is a comma list. Each is a plain name, ``[name]:port``, a hashed name
  ``|1|salt|hmac`` (HMAC-SHA1 of the lookup name), a wildcard (``*``, ``?``) or a negation
  (``!pattern``).
- The lookup name for port 22 is ``host``; for any other port it is ``[host]:port``. Names are
  compared ignoring case. A line matches when one positive pattern matches and no negated
  pattern does.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from functools import lru_cache

from portkey.keys._wire import WireError, parse_public_line

Marker = str | None  # "cert-authority", "revoked" or None
_HASHED = re.compile(r"^\|1\|([A-Za-z0-9+/=]+)\|([A-Za-z0-9+/=]+)$")
_BRACKETED = re.compile(r"^\[([^\[\]]+)\]:(\d{1,5})$")
_MARKERS = {"@cert-authority": "cert-authority", "@revoked": "revoked"}


@dataclass(frozen=True, slots=True)
class Entry:
    marker: Marker
    patterns: tuple[str, ...]  # lower-case, except hashed patterns (kept verbatim)
    key_type: str
    key: str  # "<type> <base64>" without the comment
    blob: bytes

    @property
    def hashed(self) -> bool:
        return any(p.startswith("|1|") for p in self.patterns)


def parse_known_hosts(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for raw in text.split("\n"):
        entry = parse_line(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_line(raw: str) -> Entry | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    fields = line.split(None, 1)
    marker: Marker = None
    if fields[0].startswith("@"):
        marker = _MARKERS.get(fields[0].lower())
        if marker is None or len(fields) < 2:
            return None
        fields = fields[1].split(None, 1)
    if len(fields) < 2:
        return None
    host_field, rest = fields
    try:
        pub, _ = parse_public_line(rest)
    except WireError:
        return None
    patterns = tuple(p if p.startswith("|1|") else p.lower() for p in host_field.split(",") if p)
    if not patterns:
        return None
    return Entry(marker, patterns, pub.algorithm, pub.openssh, pub.blob)


def lookup_name(host: str, port: int) -> str:
    host = host.strip().lower()
    return host if port == 22 else f"[{host}]:{port}"


def entry_matches(entry: Entry, name: str) -> bool:
    """``name`` is a ``lookup_name``."""
    positive = False
    for pattern in entry.patterns:
        if pattern.startswith("!"):
            if _pattern_matches(pattern[1:], name):
                return False
        elif not positive and _pattern_matches(pattern, name):
            positive = True
    return positive


def _pattern_matches(pattern: str, name: str) -> bool:
    if pattern.startswith("|1|"):
        return _hashed_matches(pattern, name)
    if "*" in pattern or "?" in pattern:
        return _wildcard(pattern).fullmatch(name) is not None
    return pattern == name


def _hashed_matches(pattern: str, name: str) -> bool:
    match = _HASHED.match(pattern)
    if match is None:
        return False
    try:
        salt = base64.b64decode(match.group(1), validate=True)
        expected = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        return False
    digest = hmac.new(salt, name.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(digest, expected)


@lru_cache(maxsize=256)
def _wildcard(pattern: str) -> re.Pattern[str]:
    parts = [".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern]
    return re.compile("".join(parts), re.DOTALL)


def is_wildcard(pattern: str) -> bool:
    return pattern.startswith("!") or "*" in pattern or "?" in pattern


def split_plain(pattern: str) -> tuple[str, int] | None:
    """``name`` -> (name, 22); ``[name]:port`` -> (name, port); None for hashed, wildcard or
    malformed patterns."""
    if pattern.startswith("|") or is_wildcard(pattern):
        return None
    match = _BRACKETED.match(pattern)
    if match:
        port = int(match.group(2))
        name = match.group(1)
        if not 1 <= port <= 65535:
            return None
    elif "[" in pattern or "]" in pattern:
        return None
    else:
        name, port = pattern, 22
    if not name or any(c.isspace() or c in "@/" for c in name):
        return None
    return name, port


def hash_name(name: str, salt: bytes) -> str:
    """The hashed form OpenSSH writes with ``HashKnownHosts yes`` (used by tests)."""
    digest = hmac.new(salt, name.encode("utf-8"), hashlib.sha1).digest()
    return f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"
