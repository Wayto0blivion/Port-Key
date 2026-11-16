"""Helpers shared by the package-2 repositories: tag rules, name rules and timestamps.

Private to ``portkey.store``; other packages use the repositories, not these.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from portkey.errors import ErrorCode, Invalid
from portkey.interfaces import normalize_tag
from portkey.models import TAG_PATTERN
from portkey.store.db import from_iso

MAX_TAGS_PER_CONNECTION = 32
NAME_MAX = 64

_TAG_RE = re.compile(TAG_PATTERN)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_tag(raw: str) -> str:
    """Normalise one tag and check it against ``TAG_PATTERN``. Raises ``Invalid(tag_invalid)``.

    The message never repeats the input beyond the tag itself, which is not a secret."""
    if not isinstance(raw, str):
        raise Invalid("Tags must be text.", code=ErrorCode.TAG_INVALID)
    tag = normalize_tag(raw)
    if not _TAG_RE.match(tag):
        raise Invalid(
            f"“{tag[:40]}” is not a valid tag. Use lower-case letters, digits, dot, dash and "
            "underscore, up to 32 characters.",
            code=ErrorCode.TAG_INVALID,
        )
    return tag


def clean_tags(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalise, validate and deduplicate; sorted. Does not apply the per-connection cap."""
    return tuple(sorted({clean_tag(t) for t in raw}))


def check_tag_count(tags: Iterable[str]) -> None:
    if len(set(tags)) > MAX_TAGS_PER_CONNECTION:
        raise Invalid(
            f"A connection can have at most {MAX_TAGS_PER_CONNECTION} tags.",
            code=ErrorCode.TAG_INVALID,
        )


def clean_name(raw: object, what: str) -> str:
    """Trim a connection or folder name and check 1-64 characters without control characters."""
    if not isinstance(raw, str):
        raise Invalid(f"The {what} name must be text.")
    name = raw.strip()
    if not 1 <= len(name) <= NAME_MAX or _CONTROL.search(name):
        raise Invalid(f"The {what} name must be 1 to {NAME_MAX} characters, on one line.")
    return name


def suffixed(base: str, suffix: str) -> str:
    """``base + suffix``, shortening ``base`` so the result fits ``NAME_MAX``."""
    room = NAME_MAX - len(suffix)
    return base[:room].rstrip() + suffix


def opt_iso(text: str | None) -> datetime | None:
    return from_iso(text) if text else None


def is_unique_violation(exc: sqlite3.IntegrityError) -> bool:
    return "UNIQUE constraint failed" in str(exc)
