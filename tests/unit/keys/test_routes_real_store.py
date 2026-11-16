"""Key and host routes over the real SQLite stores (no fakes): the contract between package 3
and package 2's repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from portkey.api.schemas import fp_to_id
from portkey.models import ConnectionDraft
from portkey.services import Services


async def test_keys_with_real_store(
    authed_client: httpx.AsyncClient, started: Services, ssh_dir: Path, kit: Any
) -> None:
    ed = kit.write_key(ssh_dir, "id_ed25519")
    spare = kit.write_key(ssh_dir, "spare")
    web = started.connections.create(
        ConnectionDraft(name="web-prod", host="192.168.10.42", username="john", auth_method="key")
    )
    r = await authed_client.get("/api/keys")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_fp"] == ed.fp
    by_name = {k["name"]: k for k in body["keys"]}
    assert by_name["id_ed25519"]["used_by"] == [{"id": web.id, "name": "web-prod"}]
    assert started.keys_meta.all()[ed.fp].last_path == "C:\\Users\\john\\.ssh\\id_ed25519"

    r = await authed_client.patch(f"/api/keys/{fp_to_id(ed.fp)}", json={"label": "PC key"})
    assert r.json()["label"] == "PC key"
    r = await authed_client.patch(f"/api/keys/{fp_to_id(ed.fp)}", json={"hidden": True})
    assert r.json()["error"]["code"] == "key_in_use"
    reassign = {"hidden": True, "reassign": {"auth_method": "key", "key_fp": spare.fp}}
    r = await authed_client.patch(f"/api/keys/{fp_to_id(ed.fp)}", json=reassign)
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True
    assert started.connections.get(web.id).key_fp == spare.fp

    r = await authed_client.post(
        "/api/keys/generate", json={"name": "id_portkey", "comment": "john@WORKPC"}
    )
    assert r.status_code == 201, r.text
    assert [e.kind for e in started.events.recent()] == ["key_generated"]


async def test_hosts_with_real_store(
    authed_client: httpx.AsyncClient, started: Services, ssh_dir: Path, kit: Any
) -> None:
    lines = [f"build-box {kit.write_key(ssh_dir / 'x', 'k').public_line}"]
    (ssh_dir / "known_hosts").write_text("\n".join(lines) + "\n")
    r = await authed_client.get("/api/hosts/suggestions")
    assert [s["host"] for s in r.json()["suggestions"]] == ["build-box"]
    await authed_client.post("/api/hosts/suggestions/dismiss", json={"host": "Build-Box"})
    assert (await authed_client.get("/api/hosts/suggestions")).json()["suggestions"] == []
    await authed_client.post("/api/hosts/suggestions/restore", json={"host": "build-box"})
    assert len((await authed_client.get("/api/hosts/suggestions")).json()["suggestions"]) == 1

    record = started.host_keys.add("web-prod", 22, kit.public_of(lines[0].split(" ", 1)[1]))
    r = await authed_client.get("/api/hostkeys")
    assert [k["host"] for k in r.json()["host_keys"]] == ["web-prod"]
    r = await authed_client.request(
        "DELETE", f"/api/hostkeys/{record.id}", json={"confirm_host": "WEB-PROD"}
    )
    assert r.status_code == 204
    assert started.host_keys.list_all() == []
