"""Jump chains: unknown jump, cycles, the 3-hop limit (including dependants below a changed
connection), chain resolution and delete-with-dependants."""

from __future__ import annotations

from typing import Any

import pytest

from portkey.errors import Conflict, Invalid, NotFound
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database

UNKNOWN = "5a7d2e90-1c3b-4f6e-8d2a-9b0c1d2e3f4a"


def _chain(make: Any, n: int) -> list[Any]:
    """n connections, each jumping through the previous one: c0 <- c1 <- ... <- c(n-1)."""
    out: list[Any] = []
    for i in range(n):
        out.append(make(f"hop-{i}", jump_id=out[-1].id if out else None))
    return out


def test_unknown_jump(make: Any, conns: SqliteConnectionsRepo) -> None:
    with pytest.raises(Invalid) as err:
        make("web-prod", jump_id=UNKNOWN)
    assert err.value.code == "jump_unknown"
    with pytest.raises(Invalid) as err:
        conns.validate_jump(None, UNKNOWN)
    assert err.value.code == "jump_unknown"


def test_no_jump_is_always_valid(conns: SqliteConnectionsRepo) -> None:
    conns.validate_jump(None, None)
    conns.validate_jump(UNKNOWN, None)


def test_self_jump_is_a_cycle(make: Any, conns: SqliteConnectionsRepo) -> None:
    rec = make("build-box")
    with pytest.raises(Invalid) as err:
        conns.update(rec.id, {"jump_id": rec.id})
    assert err.value.code == "jump_cycle"


def test_longer_cycle_rejected(make: Any, conns: SqliteConnectionsRepo) -> None:
    a, _b, c = _chain(make, 3)
    with pytest.raises(Invalid) as err:
        conns.update(a.id, {"jump_id": c.id})
    assert err.value.code == "jump_cycle"
    assert conns.get(a.id).jump_id is None


def test_three_hops_allowed_four_rejected(make: Any) -> None:
    chain = _chain(make, 4)  # hop-3 has 3 jumps before it
    assert chain[-1].jump_id == chain[-2].id
    with pytest.raises(Invalid) as err:
        make("too-far", jump_id=chain[-1].id)
    assert err.value.code == "jump_too_deep"
    assert "at most 3" in err.value.message


def test_depth_counts_dependants_of_the_changed_connection(
    make: Any, conns: SqliteConnectionsRepo
) -> None:
    # vpn-gw <- build-box <- web-prod ; separately a <- b.
    vpn = make("vpn-gw", host="10.8.0.1")
    build = make("build-box", host="192.168.10.20", jump_id=vpn.id)
    web = make("web-prod", jump_id=build.id)
    a = make("outer-a")
    b = make("outer-b", jump_id=a.id)
    # vpn-gw jumping through outer-b would give web-prod 4 hops: a, b, vpn-gw, build-box.
    with pytest.raises(Invalid) as err:
        conns.update(vpn.id, {"jump_id": b.id})
    assert err.value.code == "jump_too_deep"
    assert conns.get(vpn.id).jump_id is None
    conns.update(vpn.id, {"jump_id": a.id})  # 3 hops for web-prod: a, vpn-gw, build-box
    chain = [r.name for r in conns.resolve_chain(web.id)]
    assert chain == ["outer-a", "vpn-gw", "build-box", "web-prod"]


def test_resolve_chain_outermost_first(make: Any, conns: SqliteConnectionsRepo) -> None:
    a, b, c = _chain(make, 3)
    assert [r.id for r in conns.resolve_chain(c.id)] == [a.id, b.id, c.id]
    assert [r.id for r in conns.resolve_chain(a.id)] == [a.id]
    with pytest.raises(NotFound):
        conns.resolve_chain(UNKNOWN)


def test_resolve_chain_detects_a_cycle_in_bad_data(
    make: Any, conns: SqliteConnectionsRepo, db: Database
) -> None:
    a, b = _chain(make, 2)
    db.execute("UPDATE connections SET jump_id = ? WHERE id = ?", (b.id, a.id))
    with pytest.raises(Invalid) as err:
        conns.resolve_chain(b.id)
    assert err.value.code == "jump_cycle"


def test_delete_blocked_while_used_as_jump(make: Any, conns: SqliteConnectionsRepo) -> None:
    build = make("build-box", host="192.168.10.20")
    make("web-prod", jump_id=build.id)
    make("parser-vm", host="192.168.10.27", jump_id=build.id)
    with pytest.raises(Conflict) as err:
        conns.delete(build.id)
    assert err.value.code == "jump_in_use"
    assert err.value.message == (
        "build-box is the jump host for 2 connections: parser-vm, web-prod. "
        "Change their Jump via setting first."
    )
    deps = err.value.extra["dependants"]
    assert [d["name"] for d in deps] == ["parser-vm", "web-prod"]
    assert conns.find(build.id) is not None
    assert [d.name for d in conns.dependants(build.id)] == ["parser-vm", "web-prod"]


def test_delete_allowed_after_dependants_repointed(make: Any, conns: SqliteConnectionsRepo) -> None:
    build = make("build-box", host="192.168.10.20")
    web = make("web-prod", jump_id=build.id)
    conns.update(web.id, {"jump_id": None})
    conns.delete(build.id)
    assert conns.find(build.id) is None
