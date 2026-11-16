"""App password: scrypt storage, setup, login backoff, password change, bearer parsing."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from portkey.errors import Conflict, RateLimited, Unauthorized
from portkey.security import auth as auth_module
from portkey.security.auth import PasswordAuth
from portkey.security.tokens import SqliteTokenStore
from portkey.store.db import Database

# Read at import, before the autouse fixture lowers it for speed.
REAL_SCRYPT_N = auth_module.SCRYPT_N
PW = "correct horse battery"
CLIENT = "172.17.0.1"


def _fail(auth: PasswordAuth, client: str = CLIENT) -> None:
    with pytest.raises(Unauthorized) as err:
        auth.login("wrong password", client)
    assert err.value.code == "bad_password"


# --- storage -------------------------------------------------------------------------------------


def test_real_scrypt_parameters_are_strong() -> None:
    assert REAL_SCRYPT_N >= 2**15
    assert auth_module.SCRYPT_R >= 8
    assert auth_module.SALT_BYTES >= 16


def test_setup_stores_salted_scrypt_hash(auth: PasswordAuth, db: Database) -> None:
    assert not auth.is_setup()
    auth.setup(PW)
    assert auth.is_setup()
    row = db.query_one("SELECT * FROM app_auth")
    assert row is not None
    assert len(row["salt"]) == 16
    assert len(row["hash"]) == 32
    assert (row["n"], row["r"], row["p"]) == (auth_module.SCRYPT_N, 8, 1)
    assert PW.encode() not in bytes(row["hash"])
    assert PW not in "\n".join(db.conn.iterdump())


def test_two_setups_use_different_salts(tmp_path: Any, wall: Any) -> None:
    hashes = []
    for name in ("a.db", "b.db"):
        db = Database(tmp_path / name)
        db.migrate()
        PasswordAuth(db, SqliteTokenStore(db, 60.0, clock=wall)).setup(PW)
        row = db.query_one("SELECT salt, hash FROM app_auth")
        assert row is not None
        hashes.append((bytes(row["salt"]), bytes(row["hash"])))
        db.close()
    assert hashes[0][0] != hashes[1][0]
    assert hashes[0][1] != hashes[1][1]


def test_setup_returns_a_working_token(auth: PasswordAuth, tokens: SqliteTokenStore) -> None:
    issued = auth.setup(PW)
    assert tokens.verify(issued.token)


def test_setup_only_once(auth: PasswordAuth) -> None:
    auth.setup(PW)
    with pytest.raises(Conflict) as err:
        auth.setup("another password")
    assert err.value.code == "already_setup"


def test_concurrent_setup_has_one_winner(auth: PasswordAuth) -> None:
    results: list[str] = []

    def attempt(pw: str) -> None:
        try:
            auth.setup(pw)
            results.append("ok")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=attempt, args=(f"password-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "conflict", "conflict", "ok"]


def test_unicode_and_odd_passwords(auth: PasswordAuth) -> None:
    pw = "pässwörd ☃ \ud800 tail"  # a lone surrogate must not crash hashing
    auth.setup(pw)
    auth.login(pw, CLIENT)
    with pytest.raises(Unauthorized):
        auth.login(pw.strip() + "x", CLIENT)


# --- login ---------------------------------------------------------------------------------------


def test_login_before_setup(auth: PasswordAuth) -> None:
    with pytest.raises(Conflict) as err:
        auth.login(PW, CLIENT)
    assert err.value.code == "not_setup"


def test_login(auth: PasswordAuth, tokens: SqliteTokenStore) -> None:
    auth.setup(PW)
    issued = auth.login(PW, CLIENT)
    assert tokens.verify(issued.token)


def test_wrong_password_message_has_no_input(auth: PasswordAuth) -> None:
    auth.setup(PW)
    with pytest.raises(Unauthorized) as err:
        auth.login("hunter2-guess", CLIENT)
    assert "hunter2" not in str(err.value.to_body())
    assert err.value.message == "That password is not correct. Try again."


def test_backoff_first_failures_are_free(auth: PasswordAuth) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES - 1):
        _fail(auth)
    auth.login(PW, CLIENT)  # still allowed


def test_backoff_locks_out_and_doubles(auth: PasswordAuth, mono: Any) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES):
        _fail(auth)
    expected = int(auth_module.BASE_DELAY)
    for _ in range(4):
        with pytest.raises(RateLimited) as err:
            auth.login(PW, CLIENT)  # even the right password waits
        assert err.value.retry_after == expected
        assert err.value.to_body()["retry_after"] == expected
        assert err.value.code == "rate_limited"
        mono.advance(expected)
        _fail(auth)
        expected *= 2


def test_backoff_retry_after_counts_down(auth: PasswordAuth, mono: Any) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES):
        _fail(auth)
    mono.advance(3.2)
    with pytest.raises(RateLimited) as err:
        auth.login(PW, CLIENT)
    assert err.value.retry_after == 2  # ceil(5 - 3.2)
    assert auth.retry_after(CLIENT) == 2


def test_backoff_is_capped(auth: PasswordAuth, mono: Any) -> None:
    auth.setup(PW)
    for _ in range(40):
        try:
            auth.login("wrong", CLIENT)
        except RateLimited as limited:
            mono.advance(limited.retry_after)
        except Unauthorized:
            pass
    if auth.retry_after(CLIENT) is None:
        _fail(auth)
    with pytest.raises(RateLimited) as err:
        auth.login(PW, CLIENT)
    assert err.value.retry_after == int(auth_module.MAX_DELAY)


def test_lockout_rejects_without_hashing(auth: PasswordAuth, monkeypatch: Any) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES):
        _fail(auth)
    calls = []
    monkeypatch.setattr(auth_module, "hash_password", lambda *a: calls.append(a) or b"")
    with pytest.raises(RateLimited):
        auth.login(PW, CLIENT)
    assert calls == []


def test_backoff_is_per_client(auth: PasswordAuth) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES):
        _fail(auth, "192.168.10.20")
    with pytest.raises(RateLimited):
        auth.login(PW, "192.168.10.20")
    auth.login(PW, CLIENT)


def test_success_resets_the_count(auth: PasswordAuth) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES - 1):
        _fail(auth)
    auth.login(PW, CLIENT)
    for _ in range(auth_module.FREE_FAILURES - 1):
        _fail(auth)
    auth.login(PW, CLIENT)


def test_failures_are_forgotten_after_a_quiet_day(auth: PasswordAuth, mono: Any) -> None:
    auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES + 2):
        try:
            auth.login("wrong", CLIENT)
        except (Unauthorized, RateLimited):
            mono.advance(60)
    mono.advance(auth_module.FORGET_AFTER)
    # The count started again: one more typo costs nothing (without the reset it would be the
    # sixth failure and lock the client out).
    _fail(auth)
    auth.login(PW, CLIENT)


def test_client_table_is_bounded(auth: PasswordAuth, monkeypatch: Any) -> None:
    monkeypatch.setattr(auth_module, "MAX_CLIENTS", 8)
    auth.setup(PW)
    for i in range(50):
        _fail(auth, f"10.8.0.{i}")
    assert len(auth._failures) <= 8


def test_parallel_guesses_cannot_skip_the_backoff(auth: PasswordAuth) -> None:
    auth.setup(PW)
    outcomes: list[str] = []
    lock = threading.Lock()

    def guess() -> None:
        try:
            auth.login("wrong", CLIENT)
        except RateLimited:
            with lock:
                outcomes.append("limited")
        except Unauthorized:
            with lock:
                outcomes.append("checked")

    threads = [threading.Thread(target=guess) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("checked") == auth_module.FREE_FAILURES
    assert outcomes.count("limited") == 12 - auth_module.FREE_FAILURES


# --- password change -----------------------------------------------------------------------------


def test_change_password_revokes_other_tokens(auth: PasswordAuth, tokens: SqliteTokenStore) -> None:
    mine = auth.setup(PW)
    other_browser = auth.login(PW, CLIENT)
    auth.change_password(mine.token, PW, "new password 2026")
    assert tokens.verify(mine.token)
    assert not tokens.verify(other_browser.token)
    with pytest.raises(Unauthorized):
        auth.login(PW, CLIENT)
    assert tokens.verify(auth.login("new password 2026", CLIENT).token)


def test_change_password_wrong_old(auth: PasswordAuth, tokens: SqliteTokenStore) -> None:
    mine = auth.setup(PW)
    other = auth.login(PW, CLIENT)
    with pytest.raises(Unauthorized) as err:
        auth.change_password(mine.token, "not it", "new password 2026")
    assert err.value.code == "bad_password"
    assert tokens.verify(other.token)  # nothing revoked
    auth.login(PW, CLIENT)  # nothing changed


def test_change_password_wrong_old_is_rate_limited(auth: PasswordAuth) -> None:
    mine = auth.setup(PW)
    for _ in range(auth_module.FREE_FAILURES):
        with pytest.raises(Unauthorized):
            auth.change_password(mine.token, "guess", "new password 2026")
    with pytest.raises(RateLimited):
        auth.change_password(mine.token, PW, "new password 2026")


def test_change_password_gets_a_fresh_salt(auth: PasswordAuth, db: Database) -> None:
    mine = auth.setup(PW)
    before = db.query_one("SELECT salt FROM app_auth")
    auth.change_password(mine.token, PW, "new password 2026")
    after = db.query_one("SELECT salt FROM app_auth")
    assert before is not None
    assert after is not None
    assert bytes(before["salt"]) != bytes(after["salt"])


# --- authenticate --------------------------------------------------------------------------------


def test_authenticate_accepts_bearer(auth: PasswordAuth) -> None:
    token = auth.setup(PW).token
    assert auth.authenticate(f"Bearer {token}") == token
    assert auth.authenticate(f"bearer {token}") == token


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Bearer not-a-real-token-at-all-0000",
        "Token {token}",
        "{token}",
        "Bearer {token} extra",
        "Bearer {token}x",
    ],
)
def test_authenticate_refuses(auth: PasswordAuth, header: str | None) -> None:
    token = auth.setup(PW).token
    value = header.format(token=token) if header else header
    with pytest.raises(Unauthorized) as err:
        auth.authenticate(value)
    assert err.value.code == "unauthorized"
    assert err.value.status == 401


def test_authenticate_refuses_revoked_and_expired(
    auth: PasswordAuth, tokens: SqliteTokenStore, wall: Any
) -> None:
    a = auth.setup(PW).token
    b = auth.login(PW, CLIENT).token
    tokens.revoke(a)
    with pytest.raises(Unauthorized):
        auth.authenticate(f"Bearer {a}")
    wall.advance(31 * 24 * 3600.0)
    with pytest.raises(Unauthorized):
        auth.authenticate(f"Bearer {b}")
