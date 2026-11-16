"""Settings repository (package 2). Implements ``interfaces.SettingsRepo``.

Values are JSON in the ``settings`` table, one row per ``models.AppSettings`` field; missing rows
fall back to the dataclass defaults.

``update`` checks every value against the limits in docs/api.md even though the API schema
already did, because other packages call it directly. A stored value that no longer passes
(an older row, a hand edit) is ignored and the default is used instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from typing import Any

from portkey.errors import Invalid
from portkey.models import AppSettings
from portkey.store.db import Database

_FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_USER = re.compile(r"^\S{0,128}$")


def _int_between(lo: int, hi: int) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi

    return check


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


_CHECKS: dict[str, tuple[Callable[[Any], bool], str]] = {
    "default_key_fp": (
        lambda v: v is None or (isinstance(v, str) and bool(_FP.match(v))),
        "default_key_fp must be a SHA256 fingerprint or null.",
    ),
    "default_user": (
        lambda v: isinstance(v, str) and bool(_USER.match(v)),
        "The default user can't contain spaces and is at most 128 characters.",
    ),
    "passphrase_ttl": (
        _int_between(60, 7 * 86400),
        "Keep keys unlocked for 60 seconds to 7 days.",
    ),
    "detach_grace": (_int_between(60, 86400), "Keep detached sessions for 1 minute to 24 hours."),
    "font_size": (_int_between(8, 32), "Font size must be 8 to 32."),
    "windows_copy_paste": (_is_bool, "windows_copy_paste must be true or false."),
    "leave_warning": (_is_bool, "leave_warning must be true or false."),
}
FIELDS = tuple(f.name for f in fields(AppSettings))
assert set(FIELDS) == set(_CHECKS)


class SqliteSettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> AppSettings:
        stored: dict[str, Any] = {}
        for key, raw in self.db.query("SELECT key, value FROM settings"):
            if key not in _CHECKS:
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if _CHECKS[key][0](value):
                stored[key] = value
        return replace(AppSettings(), **stored)

    def update(self, changes: Mapping[str, Any]) -> AppSettings:
        unknown = set(changes) - set(_CHECKS)
        if unknown:
            raise Invalid("Unknown setting: " + ", ".join(sorted(unknown)) + ".")
        for key, value in changes.items():
            check, message = _CHECKS[key]
            if not check(value):
                raise Invalid(message)
        with self.db.tx() as c:
            c.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v)) for k, v in changes.items()],
            )
        return self.get()
