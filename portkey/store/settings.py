"""Settings repository (package 2). Implements ``interfaces.SettingsRepo``.

Values are JSON in the ``settings`` table, one row per ``models.AppSettings`` field; missing rows
fall back to the dataclass defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from portkey.models import AppSettings
from portkey.store.db import Database


class SqliteSettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> AppSettings:
        raise NotImplementedError("package 2")

    def update(self, changes: Mapping[str, Any]) -> AppSettings:
        raise NotImplementedError("package 2")
