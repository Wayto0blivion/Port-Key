"""Tags: normalisation and validation, created on first use, removed with their last use,
rename with merge, delete everywhere."""

from __future__ import annotations

from typing import Any

import pytest

from portkey.errors import Invalid, NotFound
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.tags import SqliteTagsRepo


def _tag_rows(db: Database) -> list[str]:
    return [r[0] for r in db.query("SELECT name FROM tags ORDER BY name")]


def test_tags_normalised_deduplicated_and_sorted(make: Any) -> None:
    rec = make("web-prod", tags=[" #Web ", "prod", "PROD", "web"])
    assert rec.tags == ("prod", "web")


@pytest.mark.parametrize(
    "bad", ["", "#", "-lead", ".dot", "has space", "UPPER!", "a" * 33, "ünï", "a/b"]
)
def test_invalid_tags_rejected(make: Any, bad: str) -> None:
    with pytest.raises(Invalid) as err:
        make("web-prod", tags=[bad])
    assert err.value.code == "tag_invalid"


def test_valid_edge_tags(make: Any) -> None:
    rec = make("web-prod", tags=["a", "0", "a" * 32, "x.y_z-1"])
    assert rec.tags == ("0", "a", "a" * 32, "x.y_z-1")


def test_at_most_32_tags(make: Any) -> None:
    make("ok", tags=[f"t{i}" for i in range(32)])
    with pytest.raises(Invalid) as err:
        make("too-many", tags=[f"t{i}" for i in range(33)])
    assert err.value.code == "tag_invalid"


def test_created_on_first_use_and_removed_with_last(
    make: Any, conns: SqliteConnectionsRepo, db: Database
) -> None:
    a = make("web-prod", tags=["prod", "web"])
    b = make("build-box", tags=["prod"])
    assert _tag_rows(db) == ["prod", "web"]
    conns.update(a.id, {"tags": ["prod"]})
    assert _tag_rows(db) == ["prod"]
    conns.update(b.id, {"tags": []})
    conns.delete(a.id)
    assert _tag_rows(db) == []


def test_replacing_tags_keeps_rows_for_tags_that_stay(
    make: Any, conns: SqliteConnectionsRepo, db: Database
) -> None:
    rec = make("web-prod", tags=["prod"])
    before = db.query_one("SELECT id FROM tags WHERE name = 'prod'")[0]
    conns.update(rec.id, {"tags": ["prod", "web"]})
    assert db.query_one("SELECT id FROM tags WHERE name = 'prod'")[0] == before


def test_list_most_used_first_then_name(make: Any, tags: SqliteTagsRepo) -> None:
    make("web-prod", tags=["prod", "web"])
    make("build-box", tags=["prod", "dell"])
    make("parser-vm", host="192.168.10.27", tags=["prod"])
    assert [(t.name, t.count) for t in tags.list_all()] == [("prod", 3), ("dell", 1), ("web", 1)]


def test_rename_simple(make: Any, tags: SqliteTagsRepo, conns: SqliteConnectionsRepo) -> None:
    rec = make("web-prod", tags=["prod", "web"])
    out = tags.rename("#Prod", " Production ")
    assert (out.name, out.count) == ("production", 1)
    assert conns.get(rec.id).tags == ("production", "web")


def test_rename_merges_into_existing(
    make: Any, tags: SqliteTagsRepo, conns: SqliteConnectionsRepo, db: Database
) -> None:
    a = make("web-prod", tags=["prod", "production"])
    b = make("build-box", tags=["prod"])
    c = make("vpn-gw", host="10.8.0.1", tags=["production"])
    out = tags.rename("prod", "production")
    assert (out.name, out.count) == ("production", 3)
    assert conns.get(a.id).tags == ("production",)
    assert conns.get(b.id).tags == ("production",)
    assert conns.get(c.id).tags == ("production",)
    assert _tag_rows(db) == ["production"]


def test_rename_to_same_name(make: Any, tags: SqliteTagsRepo) -> None:
    make("web-prod", tags=["prod"])
    assert tags.rename("prod", "PROD").name == "prod"


def test_rename_errors(make: Any, tags: SqliteTagsRepo) -> None:
    make("web-prod", tags=["prod"])
    with pytest.raises(NotFound):
        tags.rename("nope", "other")
    with pytest.raises(Invalid) as err:
        tags.rename("prod", "not valid")
    assert err.value.code == "tag_invalid"


def test_delete_everywhere(
    make: Any, tags: SqliteTagsRepo, conns: SqliteConnectionsRepo, db: Database
) -> None:
    a = make("web-prod", tags=["prod", "web"])
    b = make("build-box", tags=["prod"])
    tags.delete("#PROD")
    assert conns.get(a.id).tags == ("web",)
    assert conns.get(b.id).tags == ()
    assert conns.get(b.id).updated_at >= b.updated_at
    assert _tag_rows(db) == ["web"]
    with pytest.raises(NotFound):
        tags.delete("prod")
