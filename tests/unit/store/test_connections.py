"""Connections repository: CRUD, names, duplicate, pin order, usage and key reassignment."""

from __future__ import annotations

from typing import Any

import pytest

from portkey.errors import Conflict, Invalid, NotFound
from portkey.models import ConnectionDraft
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database

FP_A = "SHA256:" + "A" * 43
FP_B = "SHA256:" + "B" * 43


def test_create_fills_defaults_and_get_round_trips(conns: SqliteConnectionsRepo) -> None:
    rec = conns.create(
        ConnectionDraft(
            name="  web-prod ", host=" 192.168.10.42", username="john", auth_method="key"
        )
    )
    assert rec.name == "web-prod"
    assert rec.host == "192.168.10.42"
    assert (rec.port, rec.color, rec.pinned, rec.notes, rec.tags) == (22, "grey", False, "", ())
    assert (rec.key_fp, rec.jump_id, rec.group_id) == (None, None, None)
    assert rec.use_count == 0
    assert rec.last_used_at is None
    assert rec.created_at == rec.updated_at
    assert rec.created_at.tzinfo is not None
    assert conns.get(rec.id) == rec
    assert conns.find(rec.id) == rec
    assert len(rec.id) == 36


def test_get_unknown_raises_and_find_returns_none(conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(NotFound):
        conns.get("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a")
    assert conns.find("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a") is None


def test_names_unique_ignoring_case(make: Any) -> None:
    make("web-prod")
    with pytest.raises(Conflict) as err:
        make("WEB-PROD")
    assert err.value.code == "name_taken"
    with pytest.raises(Conflict):
        make("  Web-Prod  ")


def test_names_unique_ignoring_non_ascii_case(make: Any) -> None:
    make("Büro-gw")
    with pytest.raises(Conflict):
        make("BÜRO-GW")


def test_rename_to_own_name_in_other_case_is_allowed(
    make: Any, conns: SqliteConnectionsRepo
) -> None:
    rec = make("web-prod")
    assert conns.update(rec.id, {"name": "Web-Prod"}).name == "Web-Prod"


def test_rename_onto_another_name_conflicts(make: Any, conns: SqliteConnectionsRepo) -> None:
    make("web-prod")
    other = make("build-box")
    with pytest.raises(Conflict):
        conns.update(other.id, {"name": "web-PROD"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 65),
        ("name", "two\nlines"),
        ("host", "john@192.168.10.42"),
        ("host", "web prod"),
        ("host", "a/b"),
        ("port", 0),
        ("port", 65536),
        ("port", True),
        ("username", "has space"),
        ("auth_method", "agent"),
        ("color", "pink"),
        ("notes", "x" * 2001),
        ("key_fp", "MD5:abc"),
    ],
)
def test_create_rejects_bad_fields(make: Any, field: str, value: Any) -> None:
    fields: dict[str, Any] = {field: value}
    name = fields.pop("name", "web-prod")
    with pytest.raises(Invalid):
        make(name, **fields)


def test_update_applies_only_given_fields(make: Any, conns: SqliteConnectionsRepo) -> None:
    rec = make("web-prod", notes="Production. Red on purpose.", color="red", key_fp=FP_A)
    out = conns.update(rec.id, {"port": 2222, "pinned": True})
    assert (out.port, out.pinned, out.notes, out.color, out.key_fp) == (
        2222,
        True,
        "Production. Red on purpose.",
        "red",
        FP_A,
    )
    assert out.updated_at >= rec.updated_at


def test_update_explicit_none_clears_nullable(make: Any, conns: SqliteConnectionsRepo) -> None:
    rec = make("web-prod", key_fp=FP_A)
    assert conns.update(rec.id, {"key_fp": None}).key_fp is None


@pytest.mark.parametrize("field", ["name", "host", "port", "color", "pinned", "tags"])
def test_update_none_on_required_field_is_invalid(
    make: Any, conns: SqliteConnectionsRepo, field: str
) -> None:
    rec = make("web-prod")
    with pytest.raises(Invalid):
        conns.update(rec.id, {field: None})
    assert conns.get(rec.id) == rec


def test_update_rejects_unknown_field(make: Any, conns: SqliteConnectionsRepo) -> None:
    rec = make("web-prod")
    with pytest.raises(Invalid):
        conns.update(rec.id, {"use_count": 99})


def test_update_unknown_id(conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(NotFound):
        conns.update("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", {"port": 22})


def test_group_must_exist(make: Any, conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(Invalid) as err:
        make("web-prod", group_id="d4e5f6a7-1829-4b0c-9d8e-7f6a5b4c3d2e")
    assert err.value.code == "group_unknown"
    rec = make("build-box")
    with pytest.raises(Invalid):
        conns.update(rec.id, {"group_id": "d4e5f6a7-1829-4b0c-9d8e-7f6a5b4c3d2e"})


def test_list_orders_pinned_then_sort_order_then_name(
    make: Any, conns: SqliteConnectionsRepo
) -> None:
    make("vpn-gw", host="10.8.0.1")
    make("build-box", host="192.168.10.20")
    pinned = make("web-prod", pinned=True)
    late = make("Alpha")
    conns.update(late.id, {"sort_order": 5})
    names = [r.name for r in conns.list_all()]
    assert names == ["web-prod", "build-box", "vpn-gw", "Alpha"]
    conns.update(pinned.id, {"pinned": False})
    assert [r.name for r in conns.list_all()][-1] == "Alpha"


def test_touch_used_counts_and_ignores_unknown(make: Any, conns: SqliteConnectionsRepo) -> None:
    rec = make("web-prod")
    conns.touch_used(rec.id)
    conns.touch_used(rec.id)
    conns.touch_used("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a")
    out = conns.get(rec.id)
    assert out.use_count == 2
    assert out.last_used_at is not None
    assert out.updated_at == rec.updated_at  # usage is not an edit


def test_duplicate_copies_fields_but_not_usage_or_pin(
    make: Any, conns: SqliteConnectionsRepo
) -> None:
    jump = make("build-box", host="192.168.10.20")
    src = make(
        "web-prod",
        port=2222,
        pinned=True,
        color="red",
        notes="Production.",
        tags=["prod", "web"],
        jump_id=jump.id,
        key_fp=FP_A,
    )
    conns.touch_used(src.id)
    copy = conns.duplicate(src.id)
    assert copy.id != src.id
    assert copy.name == "web-prod (copy)"
    assert (copy.port, copy.color, copy.notes, copy.tags, copy.jump_id, copy.key_fp) == (
        2222,
        "red",
        "Production.",
        ("prod", "web"),
        jump.id,
        FP_A,
    )
    assert copy.pinned is False
    assert copy.use_count == 0
    assert copy.last_used_at is None
    assert conns.duplicate(src.id).name == "web-prod (copy 2)"
    assert conns.duplicate(src.id).name == "web-prod (copy 3)"


def test_duplicate_skips_names_taken_in_other_case(make: Any, conns: SqliteConnectionsRepo) -> None:
    src = make("web-prod")
    make("WEB-PROD (COPY)")
    assert conns.duplicate(src.id).name == "web-prod (copy 2)"


def test_duplicate_keeps_long_names_within_64(make: Any, conns: SqliteConnectionsRepo) -> None:
    src = make("x" * 64)
    copy = conns.duplicate(src.id)
    assert len(copy.name) == 64
    assert copy.name.endswith(" (copy)")


def test_duplicate_unknown(conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(NotFound):
        conns.duplicate("0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a")


def test_delete_removes_connection_and_unused_tags(
    make: Any, conns: SqliteConnectionsRepo, db: Database
) -> None:
    a = make("web-prod", tags=["prod", "web"])
    make("build-box", tags=["prod"])
    conns.delete(a.id)
    assert conns.find(a.id) is None
    assert [r[0] for r in db.query("SELECT name FROM tags")] == ["prod"]
    with pytest.raises(NotFound):
        conns.delete(a.id)


def test_using_key_includes_default_key_users(make: Any, conns: SqliteConnectionsRepo) -> None:
    make("web-prod", key_fp=FP_A)
    make("build-box")  # default key
    make("vpn-gw", auth_method="password")
    make("imaging-01", key_fp=FP_B)
    assert [r.name for r in conns.using_key(FP_A, default_fp=FP_A)] == ["build-box", "web-prod"]
    assert [r.name for r in conns.using_key(FP_A, default_fp=FP_B)] == ["web-prod"]
    assert [r.name for r in conns.using_key(FP_A, default_fp=None)] == ["web-prod"]


def test_reassign_key_to_other_key_and_to_password(make: Any, conns: SqliteConnectionsRepo) -> None:
    a = make("web-prod", key_fp=FP_A)
    b = make("build-box")
    other = make("imaging-01", key_fp=FP_B)
    changed = conns.reassign_key(FP_A, FP_A, "key", FP_B)
    assert {r.id for r in changed} == {a.id, b.id}
    assert conns.get(a.id).key_fp == FP_B
    assert conns.get(b.id).key_fp == FP_B
    changed = conns.reassign_key(FP_B, None, "password", None)
    assert {r.id for r in changed} == {a.id, b.id, other.id}
    assert all(r.auth_method == "password" and r.key_fp is None for r in conns.list_all())


def test_reassign_key_rejects_bad_method(conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(Invalid):
        conns.reassign_key(FP_A, None, "agent", None)
    with pytest.raises(Invalid):
        conns.reassign_key(FP_A, None, "key", "not-a-fingerprint")
