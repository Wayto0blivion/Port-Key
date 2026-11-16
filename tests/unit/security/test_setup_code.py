"""The first-visit setup code: generation, comparison, the shared backoff and one-shot use in
``PasswordAuth``. Where it may appear in a running app is in test_setup_code_log.py; the reset
path is in test_reset_password.py."""

from __future__ import annotations

import math
import re
import threading
from typing import Any

import pytest

from portkey.errors import Conflict, Forbidden, RateLimited
from portkey.security import auth as auth_module
from portkey.security import setup_code as sc
from portkey.security.auth import PasswordAuth
from portkey.store.db import Database

PW = "correct horse battery"
CLIENT = "172.17.0.1"
FORMAT = re.compile(rf"^[{sc.ALPHABET}]{{4}}-[{sc.ALPHABET}]{{4}}-[{sc.ALPHABET}]{{4}}$")


# --- generation ----------------------------------------------------------------------------------


def test_alphabet_has_no_look_alikes_and_codes_carry_60_bits() -> None:
    assert len(set(sc.ALPHABET)) == len(sc.ALPHABET) == 32
    assert not set("01IOil") & set(sc.ALPHABET)
    assert sc.ALPHABET.isupper()
    assert sc.BITS == 60
    assert math.log2(len(sc.ALPHABET)) * sc.LENGTH >= 60


def test_codes_are_three_groups_of_four() -> None:
    for _ in range(200):
        assert FORMAT.fullmatch(sc.generate())


def test_codes_come_from_the_csprng(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def choice(seq: str) -> str:
        seen.append(seq)
        return "K"

    monkeypatch.setattr(sc.secrets, "choice", choice)
    assert sc.generate() == "KKKK-KKKK-KKKK"
    assert seen == [sc.ALPHABET] * 12


def test_codes_do_not_repeat() -> None:
    codes = {sc.generate() for _ in range(2000)}
    assert len(codes) == 2000
    assert set("".join(codes)) - {"-"} == set(sc.ALPHABET)


# --- comparison ----------------------------------------------------------------------------------

CODE = "K7QM-3XRP-W9TD"


@pytest.mark.parametrize(
    "typed",
    [
        CODE,
        "k7qm-3xrp-w9td",
        "K7QM3XRPW9TD",
        "k7qm3xrpw9td",
        " K7QM 3XRP W9TD ",
        "k7qm-3XRP w9td",
        "K7QM–3XRP–W9TD",  # en dashes, as a word processor pastes them
        "K7QM_3XRP.W9TD",
    ],
)
def test_matches_any_case_with_or_without_separators(typed: str) -> None:
    assert sc.matches(typed, CODE)


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "-",
        "K7QM-3XRP-W9TE",
        "K7QM-3XRP-W9T",
        "K7QM-3XRP-W9TDD",
        "W9TD-3XRP-K7QM",
        "K7QM-3XRP-W9TΔ",
        "K7QM-3XRP-W9T\ud800",  # a lone surrogate must not crash the comparison
    ],
)
def test_refuses_anything_else(typed: str) -> None:
    assert not sc.matches(typed, CODE)


def test_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = sc.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(sc.hmac, "compare_digest", spy)
    assert not sc.matches("K7QM", CODE)
    # Both sides are fixed-length digests, so the length of the guess leaks nothing either.
    assert [(len(a), len(b)) for a, b in calls] == [(32, 32)]


def test_find_in_log_reads_the_newest_code_through_compose_prefixes() -> None:
    log = (
        "portkey-1  | INFO:     Started server process [1]\n"
        "portkey-1  |   Setup code:   K7QM-3XRP-W9TD\n"
        "portkey-1  | INFO:     Shutting down\n"
        "portkey-1  |   Setup code:   M4NP-H8KX-2CRV\n"
    )
    assert sc.find_in_log(log) == "M4NP-H8KX-2CRV"
    assert sc.find_in_log("portkey-1  | INFO:     Application startup complete.") is None


# --- PasswordAuth --------------------------------------------------------------------------------


def _code(auth: PasswordAuth) -> str:
    code = auth.new_setup_code()
    assert code is not None
    return code


def test_states(auth: PasswordAuth) -> None:
    assert auth.setup_code_state() == "restart"  # no password and no code held yet
    code = _code(auth)
    assert auth.setup_code_state() == "required"
    auth.setup(PW, code, CLIENT)
    assert auth.setup_code_state() is None
    assert auth.new_setup_code() is None  # a later start makes no code


def test_each_start_replaces_the_code(auth: PasswordAuth) -> None:
    first = _code(auth)
    second = _code(auth)
    assert first != second
    with pytest.raises(Forbidden) as err:
        auth.setup(PW, first, CLIENT)
    assert err.value.code == "bad_setup_code"
    auth.setup(PW, second.lower().replace("-", ""), CLIENT)
    assert auth.is_setup()


def test_wrong_code_is_403_and_changes_nothing(auth: PasswordAuth, db: Database) -> None:
    code = _code(auth)
    with pytest.raises(Forbidden) as err:
        auth.setup(PW, "AAAA-BBBB-CCCC", CLIENT)
    assert err.value.status == 403
    assert err.value.code == "bad_setup_code"
    assert "docker compose logs portkey" in err.value.message
    assert "AAAA" not in str(err.value.to_body())
    assert code not in str(err.value.to_body())
    assert db.query_one("SELECT 1 FROM app_auth") is None
    auth.setup(PW, code, CLIENT)


def test_the_code_is_one_shot(auth: PasswordAuth) -> None:
    code = _code(auth)
    auth.setup(PW, code, CLIENT)
    assert auth._setup_code is None  # discarded from memory
    with pytest.raises(Conflict) as err:
        auth.setup("another password", code, CLIENT)
    assert err.value.code == "already_setup"


def test_wrong_codes_count_against_the_login_backoff(auth: PasswordAuth, mono: Any) -> None:
    code = _code(auth)
    for _ in range(auth_module.FREE_FAILURES):
        with pytest.raises(Forbidden):
            auth.setup(PW, "AAAA-BBBB-CCCC", CLIENT)
    expected = int(auth_module.BASE_DELAY)
    for _ in range(3):
        with pytest.raises(RateLimited) as limited:
            auth.setup(PW, code, CLIENT)  # even the right code waits
        assert limited.value.retry_after == expected
        assert limited.value.message == f"Too many attempts. Try again in {expected} seconds."
        mono.advance(expected)
        with pytest.raises(Forbidden):
            auth.setup(PW, "AAAA-BBBB-CCCC", CLIENT)
        expected *= 2
    # Same per-client bucket as wrong passwords: another client is not held up.
    auth.setup(PW, code, "192.168.10.20")


def test_lockout_rejects_without_comparing(
    auth: PasswordAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _code(auth)
    for _ in range(auth_module.FREE_FAILURES):
        with pytest.raises(Forbidden):
            auth.setup(PW, "AAAA-BBBB-CCCC", CLIENT)
    calls: list[str] = []
    monkeypatch.setattr(sc, "matches", lambda a, b: calls.append(a) or True)
    with pytest.raises(RateLimited):
        auth.setup(PW, code, CLIENT)
    assert calls == []


def test_parallel_guesses_cannot_skip_the_backoff(auth: PasswordAuth) -> None:
    _code(auth)
    outcomes: list[str] = []
    lock = threading.Lock()

    def guess(i: int) -> None:
        try:
            auth.setup(PW, f"AAAA-BBBB-CC{i:02d}", CLIENT)
        except RateLimited:
            with lock:
                outcomes.append("limited")
        except Forbidden:
            with lock:
                outcomes.append("checked")

    threads = [threading.Thread(target=guess, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("checked") == auth_module.FREE_FAILURES
    assert outcomes.count("limited") == 12 - auth_module.FREE_FAILURES


def test_password_cleared_while_running_needs_a_restart(auth: PasswordAuth, db: Database) -> None:
    code = _code(auth)
    auth.setup(PW, code, CLIENT)
    db.execute("DELETE FROM app_auth")  # what python -m portkey.reset_password does
    assert auth.setup_code_state() == "restart"
    for _ in range(auth_module.FREE_FAILURES + 2):  # nothing to guess, so no backoff either
        with pytest.raises(Conflict) as err:
            auth.setup(PW, code, CLIENT)
        assert err.value.code == "setup_needs_restart"
    assert "docker compose restart portkey" in err.value.message
    fresh = _code(auth)  # the restart
    assert fresh != code
    with pytest.raises(Forbidden):
        auth.setup(PW, code, CLIENT)
    auth.setup(PW, fresh, CLIENT)
