"""/api/hosts/suggestions* and /api/hostkeys* through the ASGI transport."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncssh
import httpx

from portkey.keys._kh_parse import hash_name
from portkey.models import HostKeyRecord
from tests.unit.keys.conftest import make_conn


def _pub() -> str:
    key = asyncssh.generate_private_key("ssh-ed25519")
    return bytes(key.export_public_key("openssh")).decode().strip()


async def test_suggestions(api: httpx.AsyncClient, wired: Any, ssh_dir: Path, kit: Any) -> None:
    web, parser, img, rsa = _pub(), _pub(), _pub(), _pub()
    lines = [
        f"web-prod,192.168.10.42 {web}",
        f"parser-vm {parser}",
        f"192.168.10.27 {parser}",
        f"[imaging-01]:2222 {img}",
        f"build-box {rsa}",
        f"*.lan {rsa}",
        f"{hash_name('vpn-gw', b'x' * 20)} {rsa}",
    ]
    (ssh_dir / "known_hosts").write_bytes(kit.encode("\n".join(lines) + "\n", "utf16"))
    wired.connections.conns = [kit.make_conn("web", "192.168.10.42")]  # saved via its alias
    r = await api.get("/api/hosts/suggestions")
    assert r.status_code == 200
    assert r.json() == {
        "suggestions": [
            {"host": "build-box", "port": 22, "aliases": [], "key_types": ["ssh-ed25519"]},
            {"host": "imaging-01", "port": 2222, "aliases": [], "key_types": ["ssh-ed25519"]},
            {
                "host": "parser-vm",
                "port": 22,
                "aliases": ["192.168.10.27"],
                "key_types": ["ssh-ed25519"],
            },
        ],
        "hashed_count": 1,
    }

    r = await api.post("/api/hosts/suggestions/dismiss", json={"host": " Parser-VM ", "port": 22})
    assert r.status_code == 204
    assert wired.dismissed.items == {("parser-vm", 22)}
    hosts = [s["host"] for s in (await api.get("/api/hosts/suggestions")).json()["suggestions"]]
    assert hosts == ["build-box", "imaging-01"]

    r = await api.post("/api/hosts/suggestions/restore", json={"host": "parser-vm", "port": 22})
    assert r.status_code == 204
    hosts = [s["host"] for s in (await api.get("/api/hosts/suggestions")).json()["suggestions"]]
    assert hosts == ["build-box", "imaging-01", "parser-vm"]

    # Dismissing by an alias hides the merged suggestion too.
    await api.post("/api/hosts/suggestions/dismiss", json={"host": "192.168.10.27"})
    hosts = [s["host"] for s in (await api.get("/api/hosts/suggestions")).json()["suggestions"]]
    assert hosts == ["build-box", "imaging-01"]


async def test_no_known_hosts(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/hosts/suggestions")
    assert r.json() == {"suggestions": [], "hashed_count": 0}


async def test_dismiss_validates(api: httpx.AsyncClient) -> None:
    for body in ({"host": "a b"}, {"host": "web-prod", "port": 0}, {"host": "x", "extra": 1}):
        r = await api.post("/api/hosts/suggestions/dismiss", json=body)
        assert r.status_code == 422


def _record(
    kit: Any, key_id: int, host: str, via: str | None = None, *, distrusted: bool = False
) -> HostKeyRecord:
    return HostKeyRecord(
        id=key_id,
        host=host,
        port=22,
        key_type="ssh-ed25519",
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFake",
        fp_sha256="SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E",
        first_seen_at=kit.now,
        last_seen_at=kit.now,
        via=via,
        distrusted=distrusted,
    )


async def test_host_keys(api: httpx.AsyncClient, wired: Any, kit: Any) -> None:
    wired.host_keys.records = {7: _record(kit, 7, "192.168.10.42"), 8: _record(kit, 8, "web-prod")}
    r = await api.get("/api/hostkeys")
    assert r.status_code == 200
    [first, _] = r.json()["host_keys"]
    assert first == {
        "id": 7,
        "host": "192.168.10.42",
        "port": 22,
        "key_type": "ssh-ed25519",
        "fp_sha256": "SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E",
        "public_key": first["public_key"],
        "first_seen_at": "2026-11-15T14:02:00Z",
        "last_seen_at": "2026-11-15T14:02:00Z",
        "via_id": None,
        "via_name": None,
        "distrusted": False,
    }

    r = await api.request("DELETE", "/api/hostkeys/8", json={"confirm_host": "web-prd"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "confirm_mismatch")
    assert 8 in wired.host_keys.records

    r = await api.request("DELETE", "/api/hostkeys/8", json={"confirm_host": " WEB-PROD "})
    assert r.status_code == 204
    assert 8 not in wired.host_keys.records
    assert wired.events.recorded == [
        (
            "hostkey_forgotten",
            None,
            {
                "host": "web-prod",
                "port": 22,
                "fp": "SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E",
            },
        )
    ]

    r = await api.request("DELETE", "/api/hostkeys/99", json={"confirm_host": "web-prod"})
    assert r.status_code == 404


async def test_host_keys_name_their_route_and_distrust(
    api: httpx.AsyncClient, wired: Any, kit: Any
) -> None:
    bastion = make_conn("bastion1")
    wired.connections.conns = [bastion]
    wired.host_keys.records = {
        1: _record(kit, 1, "10.0.0.5", bastion.id),
        2: _record(kit, 2, "10.0.0.5", "00000000-0000-4000-8000-00000000dead"),
        3: _record(kit, 3, "web-prod", distrusted=True),
    }
    keys = (await api.get("/api/hostkeys")).json()["host_keys"]
    assert [(k["via_id"], k["via_name"], k["distrusted"]) for k in keys] == [
        (bastion.id, "bastion1", False),
        ("00000000-0000-4000-8000-00000000dead", None, False),  # that jump was deleted
        (None, None, True),
    ]
