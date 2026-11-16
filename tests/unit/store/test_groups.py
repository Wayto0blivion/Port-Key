"""Folders: depth limit, sibling names, moves (cycle and depth), delete re-parenting, counts."""

from __future__ import annotations

from typing import Any

import pytest

from portkey.errors import Conflict, Invalid, NotFound
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.groups import SqliteGroupsRepo

UNKNOWN = "c3d4e5f6-0718-4a9b-8c7d-6e5f4a3b2c1d"


def test_create_top_level_and_nested(groups: SqliteGroupsRepo) -> None:
    office = groups.create("  Office LAN ", None)
    servers = groups.create("Servers", office.id)
    assert office.name == "Office LAN"
    assert (office.parent_id, servers.parent_id) == (None, office.id)
    assert office.connection_count == 0
    assert groups.get(servers.id) == servers
    assert [g.name for g in groups.path(servers.id)] == ["Office LAN", "Servers"]


def test_new_folders_go_after_their_siblings(groups: SqliteGroupsRepo) -> None:
    a = groups.create("VPN", None)
    b = groups.create("Appliances", None)
    assert (a.sort_order, b.sort_order) == (0, 1)


def test_create_rejects_bad_names(groups: SqliteGroupsRepo) -> None:
    for bad in ("", "   ", "x" * 65, "a\tb"):
        with pytest.raises(Invalid):
            groups.create(bad, None)


def test_depth_limit_three_levels(groups: SqliteGroupsRepo) -> None:
    one = groups.create("Office LAN", None)
    two = groups.create("Servers", one.id)
    three = groups.create("Dell", two.id)
    with pytest.raises(Invalid) as err:
        groups.create("Too deep", three.id)
    assert err.value.code == "group_too_deep"


def test_unknown_parent(groups: SqliteGroupsRepo) -> None:
    with pytest.raises(Invalid) as err:
        groups.create("Servers", UNKNOWN)
    assert err.value.code == "group_unknown"


def test_sibling_names_unique_ignoring_case(groups: SqliteGroupsRepo) -> None:
    office = groups.create("Office LAN", None)
    groups.create("Servers", office.id)
    groups.create("Servers", None)  # same name, other parent: fine
    with pytest.raises(Conflict) as err:
        groups.create("office lan", None)
    assert err.value.code == "name_taken"
    with pytest.raises(Conflict):
        groups.create("SERVERS", office.id)


def test_rename_conflict_and_own_case_change(groups: SqliteGroupsRepo) -> None:
    vpn = groups.create("VPN", None)
    groups.create("Appliances", None)
    with pytest.raises(Conflict):
        groups.update(vpn.id, {"name": "appliances"})
    assert groups.update(vpn.id, {"name": "vpn"}).name == "vpn"


def test_move_into_itself_or_descendant_is_a_cycle(groups: SqliteGroupsRepo) -> None:
    one = groups.create("Office LAN", None)
    two = groups.create("Servers", one.id)
    for target in (one.id, two.id):
        with pytest.raises(Invalid) as err:
            groups.update(one.id, {"parent_id": target})
        assert err.value.code == "group_cycle"
    assert groups.get(one.id).parent_id is None


def test_move_checks_depth_of_whole_subtree(groups: SqliteGroupsRepo) -> None:
    a = groups.create("A", None)
    a2 = groups.create("A2", a.id)
    b = groups.create("B", None)
    groups.create("B2", b.id)
    # Moving B (height 2) under A2 (level 2) would put B2 at level 4.
    with pytest.raises(Invalid) as err:
        groups.update(b.id, {"parent_id": a2.id})
    assert err.value.code == "group_too_deep"
    moved = groups.update(b.id, {"parent_id": a.id})  # B at 2, B2 at 3
    assert moved.parent_id == a.id


def test_move_to_root_and_to_unknown(groups: SqliteGroupsRepo) -> None:
    a = groups.create("A", None)
    child = groups.create("Child", a.id)
    assert groups.update(child.id, {"parent_id": None}).parent_id is None
    with pytest.raises(Invalid) as err:
        groups.update(child.id, {"parent_id": UNKNOWN})
    assert err.value.code == "group_unknown"


def test_move_into_a_clashing_sibling_name(groups: SqliteGroupsRepo) -> None:
    a = groups.create("A", None)
    groups.create("Servers", a.id)
    loose = groups.create("servers", None)
    with pytest.raises(Conflict):
        groups.update(loose.id, {"parent_id": a.id})


def test_update_sort_order_and_unknown_fields(groups: SqliteGroupsRepo) -> None:
    a = groups.create("A", None)
    assert groups.update(a.id, {"sort_order": 7}).sort_order == 7
    with pytest.raises(Invalid):
        groups.update(a.id, {"colour": "red"})
    with pytest.raises(NotFound):
        groups.update(UNKNOWN, {"name": "B"})


def test_counts_include_descendants(groups: SqliteGroupsRepo, make: Any) -> None:
    office = groups.create("Office LAN", None)
    servers = groups.create("Servers", office.id)
    dell = groups.create("Dell", servers.id)
    make("web-prod", group_id=servers.id)
    make("build-box", group_id=dell.id)
    make("parser-vm", group_id=office.id)
    make("vpn-gw")
    counts = {g.name: g.connection_count for g in groups.list_all()}
    assert counts == {"Office LAN": 3, "Servers": 2, "Dell": 1}


def test_list_puts_roots_first(groups: SqliteGroupsRepo) -> None:
    b = groups.create("B", None)
    groups.create("Inner", b.id)
    groups.create("A", None)
    out = groups.list_all()
    assert [g.name for g in out[:2]] == ["B", "A"]  # roots by sort_order, then name
    assert out[2].name == "Inner"


def test_delete_moves_children_and_connections_up(
    groups: SqliteGroupsRepo, conns: SqliteConnectionsRepo, make: Any
) -> None:
    office = groups.create("Office LAN", None)
    servers = groups.create("Servers", office.id)
    dell = groups.create("Dell", servers.id)
    web = make("web-prod", group_id=servers.id)
    groups.delete(servers.id)
    assert groups.get(dell.id).parent_id == office.id
    assert conns.get(web.id).group_id == office.id
    with pytest.raises(NotFound):
        groups.get(servers.id)
    groups.delete(office.id)  # top level: contents go to root / Ungrouped
    assert groups.get(dell.id).parent_id is None
    assert conns.get(web.id).group_id is None
    assert len(conns.list_all()) == 1


def test_delete_renames_clashing_children(groups: SqliteGroupsRepo) -> None:
    groups.create("Servers", None)
    groups.create("Servers (2)", None)
    office = groups.create("Office LAN", None)
    inner = groups.create("servers", office.id)
    same = groups.create("Office LAN", office.id)  # same name as the folder being deleted
    groups.delete(office.id)
    assert groups.get(inner.id).name == "servers (3)"
    assert groups.get(same.id).name == "Office LAN"
    assert all(g.parent_id is None for g in groups.list_all())


def test_delete_unknown(groups: SqliteGroupsRepo) -> None:
    with pytest.raises(NotFound):
        groups.delete(UNKNOWN)


def test_delete_is_atomic(groups: SqliteGroupsRepo, make: Any, db: Database) -> None:
    office = groups.create("Office LAN", None)
    servers = groups.create("Servers", office.id)
    make("web-prod", group_id=servers.id)
    db.execute(
        "CREATE TRIGGER fail_group_delete BEFORE DELETE ON groups"
        " BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(Exception, match="test failure"):
        groups.delete(servers.id)
    assert groups.get(servers.id).parent_id == office.id
    assert groups.get(servers.id).connection_count == 1
