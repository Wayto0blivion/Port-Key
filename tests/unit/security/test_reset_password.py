"""``python -m portkey.reset_password``: the forgotten-password path (package 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from portkey.config import Limits
from portkey.models import ConnectionDraft
from portkey.reset_password import main, reset
from portkey.security.auth import PasswordAuth
from portkey.security.tokens import SqliteTokenStore
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PORTKEY_DATA_DIR", str(tmp_path))
    return tmp_path / "portkey.db"


def set_up(db_path: Path) -> tuple[str, str]:
    db = Database(db_path)
    db.migrate()
    tokens = SqliteTokenStore(db, 3600.0)
    auth = PasswordAuth(db, tokens)
    first = auth.setup("an old password").token
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
    auth.setup("a new password")  # the first-visit form works again
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
    assert "set a new password" in capsys.readouterr().out


def test_main_without_a_database(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--yes"]) == 1
    assert "Nothing to reset" in capsys.readouterr().err
    assert not data.exists()
