"""The asyncssh client that connects auth callbacks to the ``Prompter`` (package 4).

``PortKeySSHClient`` is the ``client_factory`` of one hop's authenticated connection. The host
key was checked before this connection is opened, and the connection is pinned to the accepted
key (``known_hosts`` holds only that key), so ``validate_host_public_key`` keeps asyncssh's
default of refusing anything else and no credential is sent to a host whose key was not
accepted. Auth order is publickey, password, keyboard-interactive (``AUTH_ORDER``):

- public-key auth uses the hop's key from ``KeyProvider.load_private``; an encrypted key not in
  the vault triggers ``Prompter.passphrase`` (3 tries, then ``KeyLocked``), and ``remember``
  stores it in the vault for ``settings.passphrase_ttl``;
- password auth -> ``Prompter.password`` (retry=True after a failure; 3 tries, then
  ``AuthFailed``);
- keyboard-interactive -> ``Prompter.kbdint`` round by round (2FA/OTP relayed verbatim);
- password-change requests -> ``PasswordExpired``.

Methods a ``HopTarget`` disallows are declined, but every method the server offers reaches a
callback, which is how ``offered_methods`` learns what to report on failure. A failure inside a
callback (cancelled prompt, prompt timeout, locked key) is kept in ``failure`` and the
connection is torn down with a disconnect; ``outcome_error`` turns the end of a failed sign-in
into the right ``SshError``. Secrets are only passed on to asyncssh, never kept or logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import asyncssh

from portkey.errors import BadRequest, ErrorCode, NotFound
from portkey.interfaces import (
    EventLog,
    HostKeyStore,
    KeyProvider,
    KeyVault,
    KnownHostsReader,
    Prompter,
)
from portkey.models import HopTarget, KeyRecord
from portkey.ssh.errors import (
    AuthFailed,
    KeyLocked,
    KeyUnreadable,
    PasswordExpired,
    PasswordNotAllowed,
    SshError,
)

if TYPE_CHECKING:
    from asyncssh.auth import KbdIntPrompts, KbdIntResponse, PasswordChangeResponse

log = logging.getLogger("portkey.ssh")

#: Order in which the client tries auth methods (OpenSSH puts keyboard-interactive before
#: password; Port-Key asks for a password first because its password prompt names the user and
#: host, and keyboard-interactive stays for servers that only offer that, such as 2FA).
AUTH_ORDER = ("publickey", "password", "keyboard-interactive")
SECRET_METHODS = frozenset({"password", "keyboard-interactive"})


class _AuthAborted(asyncssh.DisconnectError):
    """Raised inside an auth callback to end the connection; the cause is in ``failure``."""

    def __init__(self) -> None:
        super().__init__(asyncssh.DISC_AUTH_CANCELLED_BY_USER, "Authentication cancelled")


class PortKeySSHClient(asyncssh.SSHClient):
    def __init__(
        self,
        hop: HopTarget,
        prompter: Prompter,
        keys: KeyProvider,
        host_keys: HostKeyStore,
        known_hosts: KnownHostsReader,
        events: EventLog,
        passphrase_ttl: float,
        *,
        vault: KeyVault | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.hop = hop
        self.prompter = prompter
        self.keys = keys
        self.host_keys = host_keys
        self.known_hosts = known_hosts
        self.events = events
        self.passphrase_ttl = passphrase_ttl
        self.vault = vault
        self.max_attempts = max_attempts
        self.offered_methods: list[str] = []
        self.failure: Exception | None = None
        self.kex_done = asyncio.Event()  # set when key exchange is over and auth begins
        self.key_record: KeyRecord | None = None
        self._key_tried = False
        self._passwords = 0
        self._kbdint_attempts = 0
        self._kbdint_rounds = 0
        self._announced: set[str] = set()

    # --- asyncssh callbacks --------------------------------------------------------------------

    def begin_auth(self, username: str) -> None:
        self.kex_done.set()

    async def public_key_auth_requested(self) -> list[asyncssh.SSHKey] | None:
        self._offer("publickey")
        hop = self.hop
        if self._key_tried or not hop.allow_key or hop.key_fp is None:
            return None
        self._key_tried = True
        try:
            record = await self._key_record(hop.key_fp)
            await self._announce("publickey", key_name=record.name)
            return [await self._load_key(record)]
        except Exception as exc:
            raise self._abort(exc) from None

    async def password_auth_requested(self) -> str | None:
        self._offer("password")
        if not self.hop.allow_password:
            return None
        try:
            if self._passwords >= self.max_attempts:
                raise self._password_rejected()
            retry = self._passwords > 0
            self._passwords += 1
            await self._announce("password")
            return str(await self.prompter.password(self.hop.ref, self.hop.username, retry))
        except Exception as exc:
            raise self._abort(exc) from None

    async def kbdint_auth_requested(self) -> str | None:
        self._offer("keyboard-interactive")
        if not self.hop.allow_kbdint:
            return None
        try:
            if self._kbdint_attempts >= self.max_attempts:
                raise self._kbdint_rejected()
            self._kbdint_attempts += 1
            self._kbdint_rounds = 0
            await self._announce("keyboard-interactive")
        except Exception as exc:
            raise self._abort(exc) from None
        return ""

    async def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: KbdIntPrompts
    ) -> KbdIntResponse | None:
        questions = [(str(text), bool(echo)) for text, echo in prompts]
        if not questions and not name and not instructions:
            return []  # an empty round only moves PAM along
        retry = self._kbdint_attempts > 1 and self._kbdint_rounds == 0
        self._kbdint_rounds += 1
        try:
            answers = await self.prompter.kbdint(
                self.hop.ref, name, instructions, questions, retry=retry
            )
        except Exception as exc:
            raise self._abort(exc) from None
        return [str(a) for a in answers]

    async def password_change_requested(self, prompt: str, lang: str) -> PasswordChangeResponse:
        ref = self.hop.ref
        raise self._abort(
            PasswordExpired(
                f"{self.hop.username}'s password on {ref.name} has expired and must be changed "
                "before Port-Key can sign in. Change it with another SSH client, then try again.",
                hop=ref,
            )
        )

    # --- results -------------------------------------------------------------------------------

    def methods(self) -> list[str]:
        """Auth methods the server offered (of those Port-Key knows), in ``AUTH_ORDER``."""
        return [m for m in AUTH_ORDER if m in self.offered_methods]

    def outcome_error(self) -> SshError:
        """The error for a sign-in that ended without success (asyncssh's PermissionDenied)."""
        hop, ref = self.hop, self.hop.ref
        methods = self.methods()
        if self._passwords:
            return self._password_rejected()
        if self._kbdint_attempts:
            return self._kbdint_rejected()
        if self._key_tried:
            return AuthFailed(f"{ref.name} refused your key.", methods=methods, hop=ref)
        wants_secret = hop.allow_password or hop.allow_kbdint
        key_planned = hop.allow_key and hop.key_fp is not None
        if wants_secret and not key_planned and not SECRET_METHODS & set(methods):
            return PasswordNotAllowed(
                f"{ref.name} does not accept passwords. The server only allows key logins.",
                hop=ref,
                methods=methods,
            )
        if key_planned and "publickey" not in methods:
            return AuthFailed(
                f"{ref.name} does not accept key logins for {hop.username}.",
                methods=methods,
                hop=ref,
            )
        return AuthFailed(
            f"{ref.name} didn't accept any way Port-Key can sign in as {hop.username}.",
            methods=methods,
            hop=ref,
        )

    # --- helpers -------------------------------------------------------------------------------

    def _offer(self, method: str) -> None:
        if method not in self.offered_methods:
            self.offered_methods.append(method)

    def _abort(self, exc: Exception) -> _AuthAborted:
        if self.failure is None:
            if isinstance(exc, SshError) and exc.hop is None:
                exc.hop = self.hop.ref
            self.failure = exc
        return _AuthAborted()

    async def _announce(self, method: str, **detail: Any) -> None:
        if method not in self._announced:
            self._announced.add(method)
            await self.prompter.status("auth", self.hop.ref, method=method, **detail)

    def _password_rejected(self) -> AuthFailed:
        ref = self.hop.ref
        return AuthFailed(
            f"{ref.name} rejected the password for {self.hop.username}. Check the user name and "
            "password, then try again.",
            methods=self.methods(),
            hop=ref,
        )

    def _kbdint_rejected(self) -> AuthFailed:
        ref = self.hop.ref
        return AuthFailed(
            f"{ref.name} did not accept the answers for {self.hop.username}. Check the password "
            "and verification code, then try again.",
            methods=self.methods(),
            hop=ref,
        )

    async def _key_record(self, fp: str) -> KeyRecord:
        try:
            record = await asyncio.to_thread(self.keys.get, fp)
        except NotFound:
            raise KeyUnreadable(
                f"The key {self.hop.ref.name} signs in with is no longer in your .ssh folder. "
                "Choose another key in Edit connection.",
                hop=self.hop.ref,
            ) from None
        self.key_record = record
        return record

    async def _load_key(self, record: KeyRecord) -> asyncssh.SSHKey:
        try:
            return await asyncio.to_thread(self.keys.load_private, record.fp, None)
        except KeyLocked:
            pass
        retry = False
        for _ in range(self.max_attempts):
            passphrase, remember = await self.prompter.passphrase(self.hop.ref, record, retry)
            try:
                key = await asyncio.to_thread(self.keys.load_private, record.fp, passphrase)
            except BadRequest as exc:
                if exc.code != ErrorCode.BAD_PASSPHRASE:
                    raise
                retry = True
                continue
            finally:
                passphrase = ""  # drop the reference as soon as it is used
            if remember and self.vault is not None:
                self.vault.put(record.fp, key, self.passphrase_ttl)
            return key
        raise KeyLocked(
            f"That passphrase doesn't unlock {record.name}. Port-Key stopped after "
            f"{self.max_attempts} tries.",
            hop=self.hop.ref,
        )


def ssh_options(
    hop: HopTarget, connect_timeout: float, keepalive: float, count_max: int
) -> dict[str, object]:
    """Keyword arguments for ``asyncssh.connect`` for one hop: username, port, keepalive, the
    login timeout and the auth order publickey, password, keyboard-interactive. Nothing from the
    container's own ``~/.ssh`` (config, keys, agent, known_hosts, CA bundle) is ever loaded; the
    caller adds ``known_hosts`` pinned to the accepted key and ``tunnel``. ``connect_timeout`` is
    enforced by the caller up to the end of key exchange only, because asyncssh's own
    ``connect_timeout`` would also cover the time a person spends answering prompts."""
    return {
        "port": hop.ref.port,
        "username": hop.username,
        "config": None,
        "client_keys": None,
        "agent_path": None,
        "password": None,
        "x509_trusted_certs": None,
        "gss_host": None,
        "preferred_auth": list(AUTH_ORDER),
        "keepalive_interval": keepalive,
        "keepalive_count_max": count_max,
        "login_timeout": login_timeout(connect_timeout),
        "connect_timeout": None,
    }


def login_timeout(
    connect_timeout: float, prompt_timeout: float = 120.0, attempts: int = 3
) -> float:
    """Upper bound for a whole sign-in, prompts included: every prompt may take its full
    ``prompt_timeout``, so allow a full set of attempts plus two more prompts."""
    return connect_timeout + prompt_timeout * (attempts + 2)
