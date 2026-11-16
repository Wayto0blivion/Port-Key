"""/api/connections routes through the httpx ASGI transport (auth overridden)."""

from __future__ import annotations

from typing import Any

import httpx

UNKNOWN = "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a"


async def _create(api: httpx.AsyncClient, name: str, **fields: Any) -> dict[str, Any]:
    body = {"name": name, "host": "192.168.10.42", "username": "john", "auth_method": "key"}
    body.update(fields)
    r = await api.post("/api/connections", json=body)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def _code(r: httpx.Response) -> str:
    code: str = r.json()["error"]["code"]
    return code


async def test_create_and_list(api: httpx.AsyncClient, fake_keys: Any) -> None:
    created = await _create(api, "web-prod", tags=["#Prod", "web"], key_fp=fake_keys.a, color="red")
    assert created["tags"] == ["prod", "web"]
    assert created["key_fp"] == fake_keys.a
    assert created["created_at"].endswith("Z")
    assert created["last_used_at"] is None
    assert set(created) == {
        "id", "name", "host", "port", "username", "auth_method", "key_fp", "jump_id", "group_id",
        "tags", "color", "pinned", "notes", "sort_order", "created_at", "updated_at",
        "last_used_at", "use_count",
    }  # fmt: skip
    r = await api.get("/api/connections")
    assert r.status_code == 200
    assert r.json() == {"connections": [created]}


async def test_create_errors(api: httpx.AsyncClient, fake_keys: Any) -> None:
    await _create(api, "web-prod")
    base = {"host": "192.168.10.20", "username": "john", "auth_method": "key"}
    cases = [
        ({"name": "WEB-PROD"}, 409, "name_taken"),
        ({"name": "build-box", "jump_id": UNKNOWN}, 422, "jump_unknown"),
        ({"name": "build-box", "group_id": UNKNOWN}, 422, "group_unknown"),
        ({"name": "build-box", "key_fp": "SHA256:" + "Z" * 43}, 422, "key_unknown"),
        ({"name": "build-box", "key_fp": fake_keys.hidden}, 422, "key_unknown"),
    ]
    for extra, status, code in cases:
        r = await api.post("/api/connections", json={**base, **extra})
        assert (r.status_code, _code(r)) == (status, code), extra
    for bad in ({"name": "x", "tags": ["not valid"]}, {"name": "x", "extra": 1}, {"name": ""}):
        r = await api.post("/api/connections", json={**base, **bad})
        assert r.status_code == 422, bad
    assert len((await api.get("/api/connections")).json()["connections"]) == 1


async def test_patch_subset_null_and_errors(api: httpx.AsyncClient, fake_keys: Any) -> None:
    rec = await _create(api, "web-prod", key_fp=fake_keys.a, notes="Production.")
    r = await api.patch(f"/api/connections/{rec['id']}", json={"port": 2222, "sort_order": 3})
    assert r.status_code == 200
    assert (r.json()["port"], r.json()["sort_order"], r.json()["notes"]) == (2222, 3, "Production.")
    r = await api.patch(f"/api/connections/{rec['id']}", json={"key_fp": None})
    assert r.json()["key_fp"] is None
    r = await api.patch(f"/api/connections/{rec['id']}", json={"key_fp": fake_keys.hidden})
    assert (r.status_code, _code(r)) == (422, "key_unknown")
    r = await api.patch(f"/api/connections/{rec['id']}", json={"name": None})
    assert (r.status_code, _code(r)) == (422, "validation")
    r = await api.patch(f"/api/connections/{rec['id']}", json={"jump_id": rec["id"]})
    assert (r.status_code, _code(r)) == (422, "jump_cycle")
    r = await api.patch(f"/api/connections/{UNKNOWN}", json={"port": 22})
    assert (r.status_code, _code(r)) == (404, "not_found")


async def test_delete_and_jump_in_use(api: httpx.AsyncClient) -> None:
    build = await _create(api, "build-box", host="192.168.10.20")
    web = await _create(api, "web-prod", jump_id=build["id"])
    r = await api.request("DELETE", f"/api/connections/{build['id']}", json={})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "jump_in_use"
    assert err["dependants"] == [{"id": web["id"], "name": "web-prod"}]
    assert "build-box is the jump host for 1 connection: web-prod." in err["message"]
    r = await api.request("DELETE", f"/api/connections/{web['id']}", json={})
    assert r.status_code == 204
    assert r.content == b""
    r = await api.request("DELETE", f"/api/connections/{web['id']}", json={})
    assert r.status_code == 404


async def test_restore_after_delete_keeps_the_id(api: httpx.AsyncClient) -> None:
    rec = await _create(api, "web-prod", tags=["prod"], color="red")
    r = await api.request("DELETE", f"/api/connections/{rec['id']}", json={})
    assert r.status_code == 204
    r = await api.post(f"/api/connections/{rec['id']}/restore", json={})
    assert r.status_code == 201
    assert r.json() | {"updated_at": None} == rec | {"updated_at": None}
    r = await api.post(f"/api/connections/{rec['id']}/restore", json={})
    assert (r.status_code, _code(r)) == (404, "not_found")
    r = await api.post(f"/api/connections/{UNKNOWN}/restore", json={})
    assert r.status_code == 404


async def test_duplicate(api: httpx.AsyncClient) -> None:
    rec = await _create(api, "web-prod", pinned=True, tags=["prod"])
    r = await api.post(f"/api/connections/{rec['id']}/duplicate", json={})
    assert r.status_code == 201
    assert (r.json()["name"], r.json()["pinned"], r.json()["tags"]) == (
        "web-prod (copy)",
        False,
        ["prod"],
    )
    r = await api.post(f"/api/connections/{UNKNOWN}/duplicate", json={})
    assert r.status_code == 404


async def test_bulk(api: httpx.AsyncClient) -> None:
    a = await _create(api, "web-prod", tags=["old"])
    b = await _create(api, "build-box", tags=["old", "prod"])
    office = (await api.post("/api/groups", json={"name": "Office LAN"})).json()
    r = await api.post(
        "/api/connections/bulk",
        json={
            "ids": [a["id"], b["id"]],
            "set_group_id": office["id"],
            "add_tags": ["#Office"],
            "remove_tags": ["old"],
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()["connections"]
    assert [c["group_id"] for c in out] == [office["id"], office["id"]]
    assert [c["tags"] for c in out] == [["office"], ["office", "prod"]]
    # Omitting set_group_id leaves folders alone; null moves to Ungrouped.
    r = await api.post("/api/connections/bulk", json={"ids": [a["id"]], "add_tags": ["web"]})
    assert r.json()["connections"][0]["group_id"] == office["id"]
    r = await api.post("/api/connections/bulk", json={"ids": [a["id"]], "set_group_id": None})
    assert r.json()["connections"][0]["group_id"] is None


async def test_bulk_is_atomic(api: httpx.AsyncClient) -> None:
    a = await _create(api, "web-prod", tags=["old"])
    before = (await api.get("/api/connections")).json()
    r = await api.post(
        "/api/connections/bulk", json={"ids": [a["id"], UNKNOWN], "add_tags": ["new"]}
    )
    assert (r.status_code, _code(r)) == (404, "not_found")
    r = await api.post("/api/connections/bulk", json={"ids": [a["id"]], "set_group_id": UNKNOWN})
    assert (r.status_code, _code(r)) == (422, "group_unknown")
    r = await api.post("/api/connections/bulk", json={"ids": [a["id"]], "add_tags": ["bad tag"]})
    assert r.status_code == 422
    r = await api.post("/api/connections/bulk", json={"ids": []})
    assert r.status_code == 422
    assert (await api.get("/api/connections")).json() == before
