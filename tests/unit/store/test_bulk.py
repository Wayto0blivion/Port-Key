"""Bulk edit: folder move and tag add/remove, all or nothing."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from portkey.errors import Invalid, NotFound
from portkey.models import BulkChange
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.groups import SqliteGroupsRepo

UNKNOWN = "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a"


@pytest.fixture
def three(make: Any) -> list[Any]:
    return [
        make("web-prod", tags=["prod", "old"]),
        make("build-box", host="192.168.10.20", tags=["old"]),
        make("vpn-gw", host="10.8.0.1"),
    ]


def _snapshot(conns: SqliteConnectionsRepo, db: Database) -> tuple[Any, ...]:
    tags = db.query("SELECT id, name, created_at FROM tags ORDER BY id")
    return (conns.list_all(), [tuple(r) for r in tags])


def test_move_and_tag(
    three: list[Any], conns: SqliteConnectionsRepo, groups: SqliteGroupsRepo
) -> None:
    office = groups.create("Office LAN", None)
    ids = tuple(r.id for r in three[:2])
    out = conns.bulk_update(
        BulkChange(
            ids=ids, set_group=True, group_id=office.id, add_tags=("#Office",), remove_tags=("old",)
        )
    )
    assert [r.id for r in out] == list(ids)
    assert all(r.group_id == office.id for r in out)
    assert out[0].tags == ("office", "prod")
    assert out[1].tags == ("office",)
    assert conns.get(three[2].id).group_id is None
    assert groups.get(office.id).connection_count == 2


def test_set_group_false_leaves_folders_alone(
    make: Any, conns: SqliteConnectionsRepo, groups: SqliteGroupsRepo
) -> None:
    vpn = groups.create("VPN", None)
    rec = make("vpn-gw", group_id=vpn.id)
    out = conns.bulk_update(BulkChange(ids=(rec.id,), add_tags=("vpn",)))
    assert out[0].group_id == vpn.id
    out = conns.bulk_update(BulkChange(ids=(rec.id,), set_group=True, group_id=None))
    assert out[0].group_id is None


def test_removed_tags_disappear_when_unused(
    three: list[Any], conns: SqliteConnectionsRepo, db: Database
) -> None:
    conns.bulk_update(BulkChange(ids=tuple(r.id for r in three), remove_tags=("old", "absent")))
    assert [r[0] for r in db.query("SELECT name FROM tags")] == ["prod"]


def test_duplicate_ids_are_applied_once(three: list[Any], conns: SqliteConnectionsRepo) -> None:
    out = conns.bulk_update(BulkChange(ids=(three[0].id, three[0].id), add_tags=("web",)))
    assert len(out) == 1


def test_unknown_id_changes_nothing(
    three: list[Any], conns: SqliteConnectionsRepo, groups: SqliteGroupsRepo, db: Database
) -> None:
    office = groups.create("Office LAN", None)
    before = _snapshot(conns, db)
    with pytest.raises(NotFound):
        conns.bulk_update(
            BulkChange(
                ids=(three[0].id, UNKNOWN), set_group=True, group_id=office.id, add_tags=("new",)
            )
        )
    assert _snapshot(conns, db) == before


@pytest.mark.parametrize(
    "change",
    [
        {"add_tags": ("ok", "Not Valid")},
        {"remove_tags": ("bad tag",)},
        {"set_group": True, "group_id": "c3d4e5f6-0718-4a9b-8c7d-6e5f4a3b2c1d"},
    ],
)
def test_invalid_value_changes_nothing(
    three: list[Any], conns: SqliteConnectionsRepo, db: Database, change: dict[str, Any]
) -> None:
    before = _snapshot(conns, db)
    with pytest.raises(Invalid):
        conns.bulk_update(BulkChange(ids=tuple(r.id for r in three), **change))
    assert _snapshot(conns, db) == before


def test_tag_cap_on_one_connection_changes_nothing(
    make: Any, conns: SqliteConnectionsRepo, db: Database
) -> None:
    roomy = make("roomy")
    full = make("full", tags=[f"t{i}" for i in range(31)])
    before = _snapshot(conns, db)
    with pytest.raises(Invalid) as err:
        conns.bulk_update(BulkChange(ids=(roomy.id, full.id), add_tags=("x", "y")))
    assert err.value.code == "tag_invalid"
    assert _snapshot(conns, db) == before


def test_failure_halfway_rolls_back_everything(
    three: list[Any], db: Database, groups: SqliteGroupsRepo, conns: SqliteConnectionsRepo
) -> None:
    office = groups.create("Office LAN", None)
    before = _snapshot(conns, db)
    # Fail on the second connection's new tag link: the folder move, the tag removal and the
    # first connection's link have already been written inside the transaction by then.
    db.execute(
        "CREATE TRIGGER fail_second BEFORE INSERT ON connection_tags"
        f" WHEN NEW.connection_id = '{three[1].id}'"
        " BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="disk I/O error"):
        conns.bulk_update(
            BulkChange(
                ids=tuple(r.id for r in three),
                set_group=True,
                group_id=office.id,
                add_tags=("office",),
                remove_tags=("old",),
            )
        )
    db.execute("DROP TRIGGER fail_second")
    assert _snapshot(conns, db) == before
    assert groups.get(office.id).connection_count == 0


def test_id_count_limits(conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(Invalid):
        conns.bulk_update(BulkChange(ids=()))
    with pytest.raises(Invalid):
        conns.bulk_update(BulkChange(ids=tuple(f"id-{i}" for i in range(1001))))
