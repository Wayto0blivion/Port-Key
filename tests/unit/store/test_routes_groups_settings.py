"""/api/groups, /api/tags and /api/settings routes through the httpx ASGI transport."""

from __future__ import annotations

from typing import Any

import httpx

from portkey.config import Config

UNKNOWN = "c3d4e5f6-0718-4a9b-8c7d-6e5f4a3b2c1d"


def _code(r: httpx.Response) -> str:
    code: str = r.json()["error"]["code"]
    return code


async def _conn(api: httpx.AsyncClient, name: str, **fields: Any) -> dict[str, Any]:
    body = {"name": name, "host": "192.168.10.42", "username": "john", "auth_method": "key"}
    r = await api.post("/api/connections", json={**body, **fields})
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


# --- folders ----------------------------------------------------------------------------------


async def test_folder_crud(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/groups", json={"name": "Office LAN"})
    assert r.status_code == 201
    office = r.json()
    assert office == {
        "id": office["id"],
        "name": "Office LAN",
        "parent_id": None,
        "sort_order": 0,
        "connection_count": 0,
    }
    servers = (
        await api.post("/api/groups", json={"name": "Servers", "parent_id": office["id"]})
    ).json()
    await _conn(api, "web-prod", group_id=servers["id"])
    groups = (await api.get("/api/groups")).json()["groups"]
    assert [(g["name"], g["connection_count"]) for g in groups] == [
        ("Office LAN", 1),
        ("Servers", 1),
    ]

    r = await api.patch(f"/api/groups/{servers['id']}", json={"name": "Racks", "parent_id": None})
    assert (r.status_code, r.json()["name"], r.json()["parent_id"]) == (200, "Racks", None)

    r = await api.request("DELETE", f"/api/groups/{servers['id']}", json={})
    assert r.status_code == 204
    conns = (await api.get("/api/connections")).json()["connections"]
    assert conns[0]["group_id"] is None


async def test_folder_errors(api: httpx.AsyncClient) -> None:
    a = (await api.post("/api/groups", json={"name": "A"})).json()
    b = (await api.post("/api/groups", json={"name": "B", "parent_id": a["id"]})).json()
    c = (await api.post("/api/groups", json={"name": "C", "parent_id": b["id"]})).json()
    cases = [
        ("POST", "/api/groups", {"name": "a"}, 409, "name_taken"),
        ("POST", "/api/groups", {"name": "D", "parent_id": c["id"]}, 422, "group_too_deep"),
        ("POST", "/api/groups", {"name": "D", "parent_id": UNKNOWN}, 422, "group_unknown"),
        ("PATCH", f"/api/groups/{a['id']}", {"parent_id": c["id"]}, 422, "group_cycle"),
        ("PATCH", f"/api/groups/{UNKNOWN}", {"name": "X"}, 404, "not_found"),
        ("DELETE", f"/api/groups/{UNKNOWN}", {}, 404, "not_found"),
    ]
    for method, url, body, status, code in cases:
        r = await api.request(method, url, json=body)
        assert (r.status_code, _code(r)) == (status, code), (method, url, body)
    r = await api.post("/api/groups", json={"name": "", "parent_id": None})
    assert r.status_code == 422


# --- tags -------------------------------------------------------------------------------------


async def test_tags_list_rename_merge_delete(api: httpx.AsyncClient) -> None:
    await _conn(api, "web-prod", tags=["prod", "web"])
    await _conn(api, "build-box", tags=["prod", "production"])
    r = await api.get("/api/tags")
    assert r.json() == {
        "tags": [
            {"name": "prod", "count": 2},
            {"name": "production", "count": 1},
            {"name": "web", "count": 1},
        ]
    }
    r = await api.patch("/api/tags/prod", json={"name": "#Production"})
    assert (r.status_code, r.json()) == (200, {"name": "production", "count": 2})
    r = await api.patch("/api/tags/nope", json={"name": "other"})
    assert (r.status_code, _code(r)) == (404, "not_found")
    r = await api.patch("/api/tags/web", json={"name": "not valid"})
    assert r.status_code == 422
    r = await api.request("DELETE", "/api/tags/production", json={})
    assert r.status_code == 204
    assert (await api.get("/api/tags")).json() == {"tags": [{"name": "web", "count": 1}]}
    r = await api.request("DELETE", "/api/tags/production", json={})
    assert r.status_code == 404


# --- settings ---------------------------------------------------------------------------------


async def test_settings_get_defaults_with_about(api: httpx.AsyncClient, config: Config) -> None:
    r = await api.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == {
        "default_key_fp": None,
        "default_user": "",
        "passphrase_ttl": 28800,
        "detach_grace": 900,
        "font_size": 14,
        "windows_copy_paste": True,
        "leave_warning": True,
        "about": {
            "ssh_dir": "C:\\Users\\john\\.ssh",
            "keys_dir": "C:\\Users\\john\\.ssh\\port-key",
            "data_dir": str(config.data_dir),
            "version": config.version,
        },
    }


async def test_settings_patch(api: httpx.AsyncClient, fake_keys: Any) -> None:
    r = await api.patch(
        "/api/settings",
        json={"default_user": "john", "font_size": 16, "default_key_fp": fake_keys.a},
    )
    assert r.status_code == 200
    assert (r.json()["default_user"], r.json()["font_size"], r.json()["default_key_fp"]) == (
        "john",
        16,
        fake_keys.a,
    )
    r = await api.patch("/api/settings", json={"default_key_fp": None})
    assert r.json()["default_key_fp"] is None
    assert r.json()["font_size"] == 16


async def test_settings_patch_errors(api: httpx.AsyncClient, fake_keys: Any) -> None:
    for fp in ("SHA256:" + "Z" * 43, fake_keys.hidden):
        r = await api.patch("/api/settings", json={"default_key_fp": fp})
        assert (r.status_code, _code(r)) == (422, "key_unknown")
    r = await api.patch("/api/settings", json={"font_size": None})
    assert (r.status_code, _code(r)) == (422, "validation")
    for bad in ({"font_size": 40}, {"detach_grace": 30}, {"default_user": "a b"}, {"about": {}}):
        r = await api.patch("/api/settings", json=bad)
        assert r.status_code == 422, bad
    assert (await api.get("/api/settings")).json()["font_size"] == 14
