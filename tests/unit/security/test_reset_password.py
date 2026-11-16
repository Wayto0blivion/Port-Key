"""``python -m portkey.reset_password``: the forgotten-password path (package 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from portkey.config import Config, Limits
from portkey.models import ConnectionDraft
from portkey.reset_password import main, reset
from portkey.security.auth import PasswordAuth
from portkey.security.tokens import SqliteTokenStore
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from tests.unit.security.setup_log import codes_in_log, running

PW = "correct horse battery"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PORTKEY_DATA_DIR", str(tmp_path))
    return tmp_path / "portkey.db"


def set_up(db_path: Path) -> tuple[str, str]:
    db = Database(db_path)
    db.migrate()
    tokens = SqliteTokenStore(db, 3600.0)
    auth = PasswordAuth(db, tokens)
    code = auth.new_setup_code()
    assert code is not None
    first = auth.setup("an old password", code, "172.17.0.1").token
    second = tokens.issue().token
    SqliteConnectionsRepo(db, Limits()).create(
        ConnectionDraft(name="web-prod", host="192.168.10.42", username="john", auth_method="key")
    )
    db.close()
    return first, second


def test_reset_clears_the_password_and_every_token_and_keeps_the_rest(data: Path) -> None:
    first, second = set_up(data)
    assert reset(data) == 2
    db = Database(data)
    db.migrate()
    tokens = SqliteTokenStore(db, 3600.0)
    auth = PasswordAuth(db, tokens)
    assert auth.is_setup() is False
    assert not tokens.verify(first)
    assert not tokens.verify(second)
    assert [c.name for c in SqliteConnectionsRepo(db, Limits()).list_all()] == ["web-prod"]
    # The next start makes a fresh setup code, and the first-visit form works again with it.
    code = auth.new_setup_code()
    assert code is not None
    auth.setup("a new password", code, "172.17.0.1")
    assert auth.is_setup() is True
    db.close()


def test_main_asks_for_yes(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_up(data)
    assert main([]) == 2
    assert "--yes" in capsys.readouterr().err
    db = Database(data)
    db.migrate()
    assert PasswordAuth(db, SqliteTokenStore(db, 3600.0)).is_setup() is True
    db.close()
    assert main(["--yes"]) == 0
    out = capsys.readouterr().out
    assert "set a new password" in out
    assert "docker compose restart portkey" in out
    assert "docker compose logs portkey" in out


def test_main_without_a_database(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--yes"]) == 1
    assert "Nothing to reset" in capsys.readouterr().err
    assert not data.exists()


# --- the reset path and the setup code --------------------------------------------------------


async def test_a_reset_while_the_app_runs_does_not_reopen_setup(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    async with running(config) as (_, client):
        [code] = codes_in_log(caplog.records)
        r = await client.post("/api/auth/setup", json={"password": PW, "code": code})
        token = r.json()["token"]

        # Run while the app is up, as `docker compose exec portkey ...` does.
        assert reset(config.db_path) == 1

        status = (await client.get("/api/auth/status")).json()
        assert status == {"setup": False, "authenticated": False, "setup_code": "restart"}
        r = await client.get("/api/connections", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        # The running app holds no code (a password existed when it started), so nobody can
        # set a password now: not with the old code, not with a guess.
        for typed in (code, "AAAA-BBBB-CCCC"):
            r = await client.post("/api/auth/setup", json={"password": PW, "code": typed})
            assert (r.status_code, r.json()["error"]["code"]) == (409, "setup_needs_restart")
            assert "docker compose restart portkey" in r.json()["error"]["message"]
        assert codes_in_log(caplog.records) == [code]  # and nothing new was printed

    # The next start prints a fresh code, and setup needs exactly that one.
    async with running(config) as (_, client):
        old, fresh = codes_in_log(caplog.records)
        assert old == code
        assert fresh != code
        status = (await client.get("/api/auth/status")).json()
        assert status["setup_code"] == "required"
        r = await client.post("/api/auth/setup", json={"password": PW, "code": code})
        assert (r.status_code, r.json()["error"]["code"]) == (403, "bad_setup_code")
        r = await client.post("/api/auth/setup", json={"password": PW, "code": fresh})
        assert r.status_code == 200
        assert (await client.get("/api/auth/status")).json()["setup"] is True


async def test_a_reset_while_stopped_needs_the_code_on_the_next_start(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    async with running(config) as (_, client):
        [code] = codes_in_log(caplog.records)
        await client.post("/api/auth/setup", json={"password": PW, "code": code})
    caplog.clear()
    async with running(config):
        assert codes_in_log(caplog.records) == []  # a password exists: nothing printed
    reset(config.db_path)
    async with running(config) as (_, client):
        [fresh] = codes_in_log(caplog.records)
        assert fresh != code
        r = await client.post("/api/auth/setup", json={"password": PW, "code": "nope"})
        assert r.status_code == 403
        r = await client.post("/api/auth/setup", json={"password": PW, "code": fresh})
        assert r.status_code == 200
