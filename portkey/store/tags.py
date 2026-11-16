"""Tags repository (package 2). Implements ``interfaces.TagsRepo``.

Tags are stored lower-case and match ``models.TAG_PATTERN``. They are created by
``ConnectionsRepo`` on first assignment and deleted by the ``connection_tags_cleanup`` trigger
when their last use goes. ``rename`` onto an existing name merges the two (one transaction).
"""

from __future__ import annotations

from portkey.models import TagCount
from portkey.store.db import Database


class SqliteTagsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[TagCount]:
        raise NotImplementedError("package 2")

    def rename(self, old: str, new: str) -> TagCount:
        raise NotImplementedError("package 2")

    def delete(self, name: str) -> None:
        raise NotImplementedError("package 2")
