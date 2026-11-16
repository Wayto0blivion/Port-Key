"""GET /api/diag (package 7)."""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from pathlib import Path

import asyncssh
import httpx
import pytest
from fastapi import FastAPI

from portkey.api import routes_diag
from portkey.api.deps import require_auth
from portkey.config import Config
from portkey.errors import Unauthorized
from portkey.models import KeyProblem
from tests.unit.diag.fakes import FakeKeys, make_key

UseKeys = Callable[..., FakeKeys]


async def test_requires_auth(app: FastAPI, client: httpx.AsyncClient) -> None:
    def deny() -> str:
        raise Unauthorized()

    app.dependency_overrides[require_auth] = deny
    try:
        assert (await client.get("/api/diag")).status_code == 401
        r = await client.post("/api/diag/resolve", json={"host": "127.0.0.1", "port": 22})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"
    finally:
        app.dependency_overrides.pop(require_auth, None)


async def test_report_shape_and_versions(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    use_keys(keys=[make_key("id_ed25519")])
    r = await authed_client.get("/api/diag")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == config.version
    assert body["python"] == platform.python_version()
    assert body["asyncssh"] == asyncssh.__version__
    assert body["ssh_dir"] == {
        "path": "C:\\Users\\john\\.ssh",
        "present": True,
        "readable": True,
        "writable": True,
        "key_count": 1,
        "known_hosts": False,
        "mode_note": None,
    }
    assert body["keys_dir"] == {
        "path": "C:\\Users\\john\\.ssh\\port-key",
        "present": True,
        "readable": True,
        "writable": True,
    }
    assert body["data_dir"]["path"] == str(config.data_dir)
    assert body["data_dir"]["writable"] is True
    assert body["key_problems"] == []
    assert body["notes"] == []


async def test_key_count_counts_ssh_dir_keys_only(
    authed_client: httpx.AsyncClient, use_keys: UseKeys
) -> None:
    use_keys(
        keys=[
            make_key("id_ed25519", fp_char="A"),
            make_key("id_rsa", fp_char="B"),
            make_key("id_ed25519_portkey", source="generated", fp_char="C"),
        ]
    )
    body = (await authed_client.get("/api/diag")).json()
    assert body["ssh_dir"]["key_count"] == 2
    assert body["notes"] == []


async def test_known_hosts_and_problems(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    (config.ssh_dir / "known_hosts").write_text("build-box ssh-ed25519 AAAA\n")
    use_keys(
        keys=[make_key("id_ed25519")],
        problems=[
            KeyProblem("C:\\Users\\john\\.ssh\\work.ppk", "ppk"),
            KeyProblem("C:\\Users\\john\\.ssh\\notes", "not_private_key", "custom detail"),
            KeyProblem("C:\\Users\\john\\.ssh\\locked", "unreadable"),
        ],
    )
    body = (await authed_client.get("/api/diag")).json()
    assert body["ssh_dir"]["known_hosts"] is True
    assert body["key_problems"] == [
        {
            "path": "C:\\Users\\john\\.ssh\\work.ppk",
            "reason": "ppk",
            "detail": "PuTTY format, not supported",
        },
        {
            "path": "C:\\Users\\john\\.ssh\\notes",
            "reason": "not_private_key",
            "detail": "custom detail",
        },
        {"path": "C:\\Users\\john\\.ssh\\locked", "reason": "unreadable", "detail": "Unreadable"},
    ]


async def test_no_keys_explains_the_agent(
    authed_client: httpx.AsyncClient, use_keys: UseKeys
) -> None:
    use_keys(problems=[KeyProblem("C:\\Users\\john\\.ssh\\work.ppk", "ppk")])
    body = (await authed_client.get("/api/diag")).json()
    assert body["ssh_dir"]["key_count"] == 0
    assert body["notes"] == [routes_diag.AGENT_NOTE]


async def test_failed_key_scan_is_reported_not_raised(
    authed_client: httpx.AsyncClient, use_keys: UseKeys
) -> None:
    use_keys(error=RuntimeError("TEST-ONLY secret detail that must not leak"))
    r = await authed_client.get("/api/diag")
    assert r.status_code == 200
    body = r.json()
    assert body["ssh_dir"]["key_count"] == 0
    assert body["notes"] == ["Port-Key could not scan for keys (RuntimeError)."]
    assert "secret detail" not in r.text


async def test_missing_ssh_dir(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    use_keys()
    config.ssh_dir.rmdir()
    body = (await authed_client.get("/api/diag")).json()
    assert body["ssh_dir"]["present"] is False
    assert body["ssh_dir"]["readable"] is False
    assert body["ssh_dir"]["writable"] is False
    assert len(body["notes"]) == 1
    assert "is not mounted" in body["notes"][0]


async def test_mode_note_when_files_show_0777(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    use_keys(keys=[make_key("id_ed25519")])
    key = config.ssh_dir / "id_ed25519"
    key.write_text("TEST-ONLY")
    key.chmod(0o600)
    assert (await authed_client.get("/api/diag")).json()["ssh_dir"]["mode_note"] is None
    key.chmod(0o777)
    body = (await authed_client.get("/api/diag")).json()
    assert body["ssh_dir"]["mode_note"] == routes_diag.MODE_NOTE


async def test_ssh_dir_is_never_written(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    use_keys()
    before = sorted(os.listdir(config.ssh_dir))
    await authed_client.get("/api/diag")
    assert sorted(os.listdir(config.ssh_dir)) == before
    assert sorted(os.listdir(config.keys_dir)) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
async def test_read_only_keys_dir_is_not_writable(
    authed_client: httpx.AsyncClient, config: Config, use_keys: UseKeys
) -> None:
    use_keys(keys=[make_key("id_ed25519")])
    config.keys_dir.chmod(0o555)
    try:
        body = (await authed_client.get("/api/diag")).json()
    finally:
        config.keys_dir.chmod(0o755)
    assert body["keys_dir"]["present"] is True
    assert body["keys_dir"]["readable"] is True
    assert body["keys_dir"]["writable"] is False


def test_can_create_file_leaves_nothing_behind(tmp_path: Path) -> None:
    assert routes_diag._can_create_file(tmp_path) is True
    assert list(tmp_path.iterdir()) == []
    assert routes_diag._can_create_file(tmp_path / "missing") is False
