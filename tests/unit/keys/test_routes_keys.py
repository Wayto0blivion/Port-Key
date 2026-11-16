"""/api/keys* through the ASGI transport, with package 2's stores faked."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from portkey.api.schemas import fp_to_id


def _files(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


async def test_list_keys(api: httpx.AsyncClient, wired: Any, ssh_dir: Path, kit: Any) -> None:
    ed = kit.write_key(ssh_dir, "id_ed25519", comment="john@WORKPC")
    enc = kit.write_key(ssh_dir, "id_rsa", "ssh-rsa", "pkcs1-pem", kit.passphrase, key_size=2048)
    (ssh_dir / "work.ppk").write_text("PuTTY-User-Key-File-3: ssh-ed25519\n")
    wired.connections.conns = [
        kit.make_conn("web-prod"),  # default key
        kit.make_conn("build-box", "192.168.10.20", key_fp=enc.fp),
        kit.make_conn("vpn-gw", "10.8.0.1", auth_method="password"),
    ]
    r = await api.get("/api/keys")
    assert r.status_code == 200
    body = r.json()
    assert body["default_fp"] == ed.fp
    assert body["unlocked_count"] == 0
    assert body["problems"] == [
        {
            "path": "C:\\Users\\john\\.ssh\\work.ppk",
            "reason": "ppk",
            "detail": "PuTTY format, not supported",
        }
    ]
    by_name = {k["name"]: k for k in body["keys"]}
    first = by_name["id_ed25519"]
    assert first == {
        "id": fp_to_id(ed.fp),
        "fp": ed.fp,
        "name": "id_ed25519",
        "label": None,
        "type": "ed25519",
        "bits": 256,
        "comment": "john@WORKPC",
        "path": "C:\\Users\\john\\.ssh\\id_ed25519",
        "source": "ssh_dir",
        "encrypted": False,
        "unlocked": False,
        "public_key": ed.public_line,
        "is_default": True,
        "hidden": False,
        "used_by": [{"id": wired.connections.conns[0].id, "name": "web-prod"}],
    }
    rsa = by_name["id_rsa"]
    assert (rsa["encrypted"], rsa["bits"], rsa["is_default"]) == (True, 2048, False)
    assert [u["name"] for u in rsa["used_by"]] == ["build-box"]


async def test_public_key(api: httpx.AsyncClient, ssh_dir: Path, kit: Any) -> None:
    ed = kit.write_key(ssh_dir, "id_ed25519", encoding="utf16")
    r = await api.get(f"/api/keys/{fp_to_id(ed.fp)}/public")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == ed.public_line + "\n"
    for bad in ("A" * 43, "not-an-id", "%2E%2E"):
        r = await api.get(f"/api/keys/{bad}/public")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "key_unknown"


async def test_name_check(api: httpx.AsyncClient, keys_dir: Path, kit: Any) -> None:
    r = await api.get("/api/keys/name-check", params={"name": "id_ed25519_portkey"})
    assert r.json() == {"name": "id_ed25519_portkey", "available": True, "reason": None}
    kit.write_key(keys_dir, "id_ed25519_portkey")
    r = await api.get("/api/keys/name-check", params={"name": "id_ed25519_portkey"})
    assert r.json() == {"name": "id_ed25519_portkey", "available": False, "reason": "taken"}
    r = await api.get("/api/keys/name-check", params={"name": "../../etc/x"})
    assert r.json()["reason"] == "invalid"


async def test_generate(api: httpx.AsyncClient, wired: Any, keys_dir: Path, kit: Any) -> None:
    body = {
        "name": "id_ed25519_portkey",
        "type": "ed25519",
        "bits": None,
        "comment": "john@WORKPC port-key 2026-11-14",
        "passphrase": kit.passphrase,
    }
    r = await api.post("/api/keys/generate", json=body)
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["source"] == "generated"
    assert key["encrypted"] is True
    assert key["path"] == "C:\\Users\\john\\.ssh\\port-key\\id_ed25519_portkey"
    assert key["comment"] == "john@WORKPC port-key 2026-11-14"
    assert kit.passphrase not in r.text
    assert (keys_dir / "id_ed25519_portkey").exists()
    assert wired.events.recorded == [
        ("key_generated", None, {"fp": key["fp"], "key_type": "ed25519"})
    ]
    r = await api.post("/api/keys/generate", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "key_name_taken"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": "config"}, 422, "key_name_invalid"),
        ({"name": "../evil"}, 422, "validation"),
        ({"name": "x.pub"}, 422, "validation"),
        ({"name": "id_x", "type": "rsa", "bits": 1024}, 422, "validation"),
        ({"name": "id_x", "type": "ed25519", "bits": 256}, 422, "validation"),
        ({"name": "id_x", "comment": "line\nbreak"}, 422, "validation"),
        ({"name": "id_x", "extra": 1}, 422, "validation"),
    ],
)
async def test_generate_rejects(
    api: httpx.AsyncClient, keys_dir: Path, body: dict[str, Any], status: int, code: str
) -> None:
    r = await api.post("/api/keys/generate", json=body)
    assert r.status_code == status, r.text
    if code != "validation":  # the 422 envelope for schema errors belongs to package 1
        assert r.json()["error"]["code"] == code
    assert _files(keys_dir) == []


async def test_label_hide_and_unhide(
    api: httpx.AsyncClient, wired: Any, ssh_dir: Path, kit: Any
) -> None:
    kit.write_key(ssh_dir, "id_ed25519")
    other = kit.write_key(ssh_dir, "spare")
    kid = fp_to_id(other.fp)
    r = await api.patch(f"/api/keys/{kid}", json={"label": "  Spare key  "})
    assert r.status_code == 200
    assert r.json()["label"] == "Spare key"
    r = await api.patch(f"/api/keys/{kid}", json={"hidden": True})
    assert (r.status_code, r.json()["hidden"]) == (200, True)
    listed = (await api.get("/api/keys")).json()["keys"]
    assert {k["name"]: k["hidden"] for k in listed} == {"id_ed25519": False, "spare": True}
    r = await api.patch(f"/api/keys/{kid}", json={"hidden": False, "label": None})
    assert (r.json()["hidden"], r.json()["label"]) == (False, None)
    r = await api.patch(f"/api/keys/{'B' * 43}", json={"hidden": True})
    assert r.json()["error"]["code"] == "key_unknown"


async def test_hide_used_key_needs_reassign(
    api: httpx.AsyncClient, wired: Any, ssh_dir: Path, kit: Any
) -> None:
    ed = kit.write_key(ssh_dir, "id_ed25519")
    spare = kit.write_key(ssh_dir, "spare")
    wired.settings.update({"default_key_fp": ed.fp})
    wired.connections.conns = [
        kit.make_conn("web-prod"),
        kit.make_conn("vpn-gw", "10.8.0.1", key_fp=ed.fp),
        kit.make_conn("build-box", "192.168.10.20", key_fp=spare.fp),
    ]
    kid = fp_to_id(ed.fp)
    r = await api.patch(f"/api/keys/{kid}", json={"hidden": True})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "key_in_use"
    assert err["message"] == (
        "id_ed25519 is used by 2 connections: web-prod, vpn-gw. Choose what they use instead."
    )
    assert [d["name"] for d in err["dependants"]] == ["web-prod", "vpn-gw"]

    bad = {"hidden": True, "reassign": {"auth_method": "key", "key_fp": ed.fp}}
    r = await api.patch(f"/api/keys/{kid}", json=bad)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "key_unknown")
    assert wired.meta.rows[ed.fp].hidden is False

    good = {"hidden": True, "reassign": {"auth_method": "key", "key_fp": spare.fp}}
    r = await api.patch(f"/api/keys/{kid}", json=good)
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True
    assert r.json()["is_default"] is False
    assert wired.settings.value.default_key_fp is None
    assert {c.name: c.key_fp for c in wired.connections.conns} == {
        "web-prod": spare.fp,
        "vpn-gw": spare.fp,
        "build-box": spare.fp,
    }


async def test_hide_reassign_to_password(
    api: httpx.AsyncClient, wired: Any, ssh_dir: Path, kit: Any
) -> None:
    ed = kit.write_key(ssh_dir, "id_ed25519")
    wired.connections.conns = [kit.make_conn("web-prod")]
    body = {"hidden": True, "reassign": {"auth_method": "password"}}
    r = await api.patch(f"/api/keys/{fp_to_id(ed.fp)}", json=body)
    assert r.status_code == 200
    [conn] = wired.connections.conns
    assert (conn.auth_method, conn.key_fp) == ("password", None)


async def test_unlock_and_lock(
    api: httpx.AsyncClient,
    wired: Any,
    ssh_dir: Path,
    kit: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    plain = kit.write_key(ssh_dir, "id_ed25519")
    enc = kit.write_key(ssh_dir, "id_enc", passphrase=kit.passphrase)
    kid = fp_to_id(enc.fp)
    r = await api.post(f"/api/keys/{kid}/unlock", json={"passphrase": "nope-not-it"})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "bad_passphrase")
    assert r.json()["error"]["message"] == "That passphrase doesn't unlock id_enc. Try again."
    r = await api.post(f"/api/keys/{fp_to_id(plain.fp)}/unlock", json={"passphrase": "x"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "key_not_encrypted")
    r = await api.post(f"/api/keys/{'C' * 43}/unlock", json={"passphrase": "x"})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "key_unknown")

    wired.settings.update({"passphrase_ttl": 3600})
    r = await api.post(f"/api/keys/{kid}/unlock", json={"passphrase": kit.passphrase})
    assert r.status_code == 204
    listed = (await api.get("/api/keys")).json()
    assert listed["unlocked_count"] == 1
    assert {k["name"]: k["unlocked"] for k in listed["keys"]}["id_enc"] is True
    wired.clock.now += 3599
    assert wired.services.vault.unlocked() == [enc.fp]
    wired.clock.now += 1
    assert wired.services.vault.unlocked() == []

    body = {"passphrase": kit.passphrase, "remember": False}
    assert (await api.post(f"/api/keys/{kid}/unlock", json=body)).status_code == 204
    wired.clock.now += 59
    assert wired.services.vault.unlocked() == [enc.fp]
    r = await api.post("/api/keys/lock", json={})
    assert r.status_code == 204
    assert (await api.get("/api/keys")).json()["unlocked_count"] == 0
    assert kit.passphrase not in caplog.text
    assert "nope-not-it" not in caplog.text
