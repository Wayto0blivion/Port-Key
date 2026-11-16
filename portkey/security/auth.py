"""App password and login (package 1). Implements ``interfaces.AppAuth``.

One app password, hashed with ``hashlib.scrypt`` (salt and n/r/p stored in ``app_auth``),
compared in constant time. Failed logins get per-client exponential backoff (``RateLimited``
with ``retry_after``; the UI says "Too many attempts. Try again in 30 seconds."). Changing the
password revokes every token except the caller's.

Logins and password changes are serialised behind one lock, so parallel guesses cannot slip
past the backoff check while a hash is being computed. Wrong old passwords on a password change
share one backoff bucket, so a stolen token cannot be used to brute-force the password itself.

First-visit setup needs the one-time setup code (``security.setup_code``): ``new_setup_code``
makes one at app start while no password exists, ``setup`` checks it in constant time (wrong
codes count against the same per-client backoff as wrong passwords) and drops it once the
password is set. It lives only in this object's memory. If the password is cleared while the
app runs (``python -m portkey.reset_password``), no code is held, so setup is refused with
``setup_needs_restart`` until a restart prints a fresh one.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from portkey.errors import Conflict, ErrorCode, Forbidden, RateLimited, Unauthorized
from portkey.interfaces import TokenStore
from portkey.models import IssuedToken, SetupCodeState
from portkey.security import setup_code
from portkey.security.redact import hide_setup_code
from portkey.store.db import Database, now_iso

# scrypt cost. n=2**15, r=8 needs 32 MiB and takes roughly 50-100 ms. The parameters are stored
# with each hash, so raising them later only affects new passwords.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * 1024 * 1024
SALT_BYTES = 16
HASH_BYTES = 32

# Backoff: the first FREE_FAILURES wrong passwords cost nothing (typos); each one after that
# locks the client out for BASE_DELAY * 2**k seconds, capped at MAX_DELAY. A client's record is
# forgotten FORGET_AFTER seconds after its last failure, or on a successful login.
FREE_FAILURES = 3
BASE_DELAY = 5.0
MAX_DELAY = 600.0
FORGET_AFTER = 24 * 3600.0
MAX_CLIENTS = 1024
_CHANGE_BUCKET = "\x00change-password"

_BEARER = re.compile(r"Bearer +([^\s]+)", re.IGNORECASE)

WRONG_LOGIN_MESSAGE = "That password is not correct. Try again."
WRONG_CODE_MESSAGE = (
    "That setup code is not correct. Use the newest code in the log "
    f"(docker compose logs {setup_code.COMPOSE_SERVICE})."
)
NEEDS_RESTART_MESSAGE = (
    "Port-Key has no setup code right now: its password was cleared while it was running. "
    f"Restart it on this PC (docker compose restart {setup_code.COMPOSE_SERVICE}), then use "
    f"the code that docker compose logs {setup_code.COMPOSE_SERVICE} shows."
)


@dataclass(slots=True)
class _Failures:
    count: int = 0
    last: float = 0.0
    locked_until: float = 0.0


def hash_password(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8", "surrogatepass"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=SCRYPT_MAXMEM,
        dklen=HASH_BYTES,
    )


class PasswordAuth:
    def __init__(
        self, db: Database, tokens: TokenStore, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.db = db
        self.tokens = tokens
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, _Failures] = {}
        self._setup_code: str | None = None  # memory only; never logged here or returned

    # -- password storage ---------------------------------------------------------------------

    def is_setup(self) -> bool:
        return self.db.query_one("SELECT 1 FROM app_auth WHERE id = 1") is not None

    def _new_hash_row(self, password: str) -> tuple[bytes, bytes, int, int, int]:
        n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
        salt = os.urandom(SALT_BYTES)
        return salt, hash_password(password, salt, n, r, p), n, r, p

    def _check(self, password: str) -> bool:
        row = self.db.query_one("SELECT salt, hash, n, r, p FROM app_auth WHERE id = 1")
        if row is None:
            raise Conflict("Set a password for Port-Key first.", code=ErrorCode.NOT_SETUP)
        candidate = hash_password(password, bytes(row["salt"]), row["n"], row["r"], row["p"])
        return hmac.compare_digest(candidate, bytes(row["hash"]))

    # -- first-visit setup -----------------------------------------------------------------------

    def new_setup_code(self) -> str | None:
        """At app start: a fresh code while no password exists (the caller prints it once),
        else None. Any code held before is dropped either way."""
        with self._lock:
            self._drop_setup_code()
            if self.is_setup():
                return None
            code = setup_code.generate()
            self._setup_code = code
            hide_setup_code(code)
            return code

    def setup_code_state(self) -> SetupCodeState | None:
        if self.is_setup():
            self._drop_setup_code()
            return None
        return "required" if self._setup_code is not None else "restart"

    def _drop_setup_code(self) -> None:
        if self._setup_code is not None:
            self._setup_code = None
            hide_setup_code(None)

    def setup(self, password: str, code: str, client: str) -> IssuedToken:
        with self._lock:
            if self.is_setup():
                raise Conflict("Port-Key already has a password.", code=ErrorCode.ALREADY_SETUP)
            now = self.clock()
            self._gate(client, now)
            held = self._setup_code
            if held is None:
                raise Conflict(NEEDS_RESTART_MESSAGE, code=ErrorCode.SETUP_NEEDS_RESTART)
            if not setup_code.matches(code, held):
                self._fail(client, now)
                raise Forbidden(WRONG_CODE_MESSAGE, code=ErrorCode.BAD_SETUP_CODE)
            salt, digest, n, r, p = self._new_hash_row(password)
            with self.db.tx() as c:
                if c.execute("SELECT 1 FROM app_auth WHERE id = 1").fetchone() is not None:
                    raise Conflict("Port-Key already has a password.", code=ErrorCode.ALREADY_SETUP)
                c.execute(
                    "INSERT INTO app_auth (id, salt, hash, n, r, p, updated_at)"
                    " VALUES (1, ?, ?, ?, ?, ?, ?)",
                    (salt, digest, n, r, p, now_iso()),
                )
                issued = self.tokens.issue()
            self._drop_setup_code()
            self._failures.pop(client, None)
            return issued

    # -- backoff ------------------------------------------------------------------------------

    def _gate(self, client: str, now: float) -> None:
        state = self._failures.get(client)
        if state is None:
            return
        if now - state.last >= FORGET_AFTER:
            del self._failures[client]
            return
        if now < state.locked_until:
            raise RateLimited(retry_after=max(1, math.ceil(state.locked_until - now)))

    def _fail(self, client: str, now: float) -> None:
        state = self._failures.get(client)
        if state is None:
            self._prune(now)
            state = self._failures[client] = _Failures()
        state.count += 1
        state.last = now
        if state.count >= FREE_FAILURES:
            delay = min(MAX_DELAY, BASE_DELAY * 2 ** (state.count - FREE_FAILURES))
            state.locked_until = now + delay

    def _prune(self, now: float) -> None:
        if len(self._failures) < MAX_CLIENTS:
            return
        for key in [k for k, v in self._failures.items() if now - v.last >= FORGET_AFTER]:
            del self._failures[key]
        while len(self._failures) >= MAX_CLIENTS:
            oldest = min(self._failures, key=lambda k: self._failures[k].last)
            del self._failures[oldest]

    def retry_after(self, client: str) -> int | None:
        """Seconds until ``client`` may try again, or None when it is not locked out."""
        state = self._failures.get(client)
        now = self.clock()
        if state is None or now >= state.locked_until:
            return None
        return max(1, math.ceil(state.locked_until - now))

    # -- interface ----------------------------------------------------------------------------

    def login(self, password: str, client: str) -> IssuedToken:
        if not self.is_setup():
            raise Conflict("Set a password for Port-Key first.", code=ErrorCode.NOT_SETUP)
        with self._lock:
            now = self.clock()
            self._gate(client, now)
            if not self._check(password):
                self._fail(client, now)
                raise Unauthorized(WRONG_LOGIN_MESSAGE, code=ErrorCode.BAD_PASSWORD)
            self._failures.pop(client, None)
        return self.tokens.issue()

    def change_password(self, token: str, old: str, new: str) -> None:
        with self._lock:
            now = self.clock()
            self._gate(_CHANGE_BUCKET, now)
            if not self._check(old):
                self._fail(_CHANGE_BUCKET, now)
                raise Unauthorized(
                    "Your current password is not correct.", code=ErrorCode.BAD_PASSWORD
                )
            self._failures.pop(_CHANGE_BUCKET, None)
            salt, digest, n, r, p = self._new_hash_row(new)
            with self.db.tx() as c:
                c.execute(
                    "UPDATE app_auth SET salt = ?, hash = ?, n = ?, r = ?, p = ?, updated_at = ?"
                    " WHERE id = 1",
                    (salt, digest, n, r, p, now_iso()),
                )
                self.tokens.revoke_all_except(token)
            # Failed logins from before the change no longer say anything about the new
            # password.
            self._failures.clear()

    def authenticate(self, authorization: str | None) -> str:
        match = _BEARER.fullmatch(authorization.strip()) if authorization else None
        if match is None or not self.tokens.verify(match.group(1)):
            raise Unauthorized()
        return match.group(1)
