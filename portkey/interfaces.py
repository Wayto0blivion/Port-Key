"""Protocols between packages (frozen contract).

Threading and async rules, for every Protocol below unless a method says otherwise:

- Repositories (``*Repo``, ``HostKeyStore``, ``EventLog``, ``TokenStore``) are synchronous and
  thread-safe: one SQLite connection behind a re-entrant lock. Sync FastAPI routes call them
  directly (thread pool). Async code on a hot path (connecting, terminal sessions, the wizard)
  calls them through ``asyncio.to_thread``: while a thread-pool route holds the lock (a
  1000-id bulk edit takes about 50 ms), a direct call would stall the event loop and every
  terminal with it. ``services.db.tx()`` is re-entrant on one thread, so several repository
  calls can share one transaction (see docs/handoff/phase-a.md).
- Anything that touches the network or waits for the browser is ``async``.
- Errors are the classes in ``portkey.errors`` (HTTP) and ``portkey.ssh.errors`` (SSH/WS).
- Fingerprints are always ``"SHA256:<base64 without padding>"``.
- Secrets (passwords, passphrases, private keys, tokens, the setup code) are never logged,
  never put in an exception message and never stored, except the scrypt hash and token hashes.
  The setup code's one startup announcement is the single exception to "never logged".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from portkey.models import (
    AppSettings,
    BulkChange,
    ConnectionDraft,
    ConnectionRecord,
    ConnectionRef,
    EventKind,
    EventRecord,
    GroupRecord,
    HopRef,
    HopTarget,
    HostKeyRecord,
    IssuedToken,
    KeyGenSpec,
    KeyProblem,
    KeyRecord,
    KeysMetaRecord,
    KeySource,
    KnownHostsMatch,
    SessionSummary,
    SetupCodeState,
    SuggestionScan,
    TagCount,
)

if TYPE_CHECKING:
    import asyncssh


# --- storage (package 2) ------------------------------------------------------------------------


class ConnectionsRepo(Protocol):
    def list_all(self) -> list[ConnectionRecord]:
        """All connections ordered by pinned desc, sort_order, name (case-insensitive)."""

    def get(self, conn_id: str) -> ConnectionRecord:
        """Raises ``NotFound``."""

    def find(self, conn_id: str) -> ConnectionRecord | None: ...

    def create(self, draft: ConnectionDraft) -> ConnectionRecord:
        """Insert with a new uuid4. Validates as ``update`` does. Tags are normalised (lower-case,
        deduplicated) and created on first use. Raises ``Conflict(name_taken)``,
        ``Invalid(jump_unknown|jump_cycle|jump_too_deep|group_unknown|tag_invalid)``."""

    def update(self, conn_id: str, changes: Mapping[str, Any]) -> ConnectionRecord:
        """Apply only the keys present in ``changes`` (an explicit None clears a nullable field).
        ``tags`` replaces the whole tag set. Bumps ``updated_at``. Same errors as ``create``
        plus ``NotFound``."""

    def delete(self, conn_id: str) -> None:
        """Raises ``NotFound``, or ``Conflict(jump_in_use, dependants=[{id,name}])`` while other
        connections use it as their jump. Removes its tag links (and tags left unused). The
        deleted record is kept in memory for ``restore`` (Undo) for a few minutes."""

    def restore(self, conn_id: str) -> ConnectionRecord:
        """Put back a connection deleted in the last few minutes with its id, tags, usage and
        dates, so open tabs, host-key records and Recent still match it. A folder deleted
        since leaves it Ungrouped. Raises ``NotFound`` (nothing to restore),
        ``Conflict(name_taken)`` and ``Invalid(jump_unknown|jump_cycle|jump_too_deep)``."""

    def duplicate(self, conn_id: str) -> ConnectionRecord:
        """Copy every field except id, timestamps and usage; pinned is False; name gets
        " (copy)", then " (copy 2)", ... until unique."""

    def dependants(self, conn_id: str) -> list[ConnectionRef]:
        """Connections whose ``jump_id`` is ``conn_id``."""

    def resolve_chain(self, conn_id: str) -> list[ConnectionRecord]:
        """Outermost jump first, target last. Raises ``NotFound``, ``Invalid(jump_cycle)``."""

    def validate_jump(self, conn_id: str | None, jump_id: str | None) -> None:
        """Check that ``conn_id`` (None = a new connection) may use ``jump_id``: it exists, no
        cycle, at most 3 hops before the target. Raises ``Invalid(jump_*)``."""

    def touch_used(self, conn_id: str) -> None:
        """Set last_used_at = now and increment use_count. Unknown ids are ignored."""

    def using_key(self, fp: str, default_fp: str | None) -> list[ConnectionRef]:
        """Key-auth connections with ``key_fp == fp``, plus those with key_fp None when
        ``fp == default_fp``."""

    def reassign_key(
        self, fp: str, default_fp: str | None, auth_method: str, key_fp: str | None
    ) -> list[ConnectionRef]:
        """Atomically point every connection from ``using_key(fp, default_fp)`` at the new
        auth method/key. Returns the connections changed."""

    def bulk_update(self, change: BulkChange) -> list[ConnectionRecord]:
        """Apply folder and tag changes to every id in one transaction. Raises ``NotFound``
        (any unknown id; nothing is changed), ``Invalid(group_unknown|tag_invalid)``."""


class GroupsRepo(Protocol):
    """Folders: a tree at most 3 levels deep with case-insensitively unique sibling names."""

    def list_all(self) -> list[GroupRecord]:
        """Flat list ordered by parent, sort_order, name; counts include descendants."""

    def get(self, group_id: str) -> GroupRecord:
        """Raises ``NotFound``."""

    def create(self, name: str, parent_id: str | None) -> GroupRecord:
        """Raises ``Conflict(name_taken)``, ``Invalid(group_unknown|group_too_deep)``."""

    def update(self, group_id: str, changes: Mapping[str, Any]) -> GroupRecord:
        """Keys: name, parent_id, sort_order. Moving checks depth (including the moved
        subtree's height) and rejects moving under itself or a descendant
        (``Invalid(group_cycle)``)."""

    def delete(self, group_id: str) -> None:
        """Re-parent child folders and connections to this folder's parent (or the root /
        ungrouped), then delete. Never deletes connections. Sibling name clashes created by
        the move are resolved by suffixing " (2)", " (3)", ..."""

    def path(self, group_id: str) -> list[GroupRecord]:
        """Root first. Raises ``NotFound``."""


class TagsRepo(Protocol):
    def list_all(self) -> list[TagCount]:
        """Tags in use, most used first, then by name."""

    def rename(self, old: str, new: str) -> TagCount:
        """Rename everywhere; if ``new`` exists, merge into it. Raises ``NotFound``,
        ``Invalid(tag_invalid)``."""

    def delete(self, name: str) -> None:
        """Remove from every connection. Raises ``NotFound``."""


def normalize_tag(raw: str) -> str:
    """Canonical tag form: stripped, lower-case, a leading '#' removed. Validation against
    ``models.TAG_PATTERN`` is the caller's job."""
    return raw.strip().lstrip("#").lower()


class HostKeyStore(Protocol):
    """Host-key decisions in the app database. Hosts are stored lower-case. Every record is
    scoped by ``via``, the jump connection the host is reached through (None: directly); see
    ``models.HostKeyRecord``."""

    def list_all(self) -> list[HostKeyRecord]: ...

    def get(self, key_id: int) -> HostKeyRecord:
        """Raises ``NotFound``."""

    def for_host(self, host: str, port: int, via: str | None = None) -> list[HostKeyRecord]:
        """Every record for host:port on that route, trusted and distrusted."""

    def add(self, host: str, port: int, public_key: str, via: str | None = None) -> HostKeyRecord:
        """Trust a key (idempotent on host, port, route, key; refreshes last_seen_at; clears
        a distrust of the same key)."""

    def replace(
        self,
        host: str,
        port: int,
        public_key: str,
        via: str | None = None,
        *,
        supersede: Sequence[str] = (),
    ) -> HostKeyRecord:
        """Delete every trusted app record for host:port on that route, mark each key in
        ``supersede`` (known_hosts keys the owner replaced) distrusted there, then trust this
        key. One transaction."""

    def touch(self, key_id: int) -> None:
        """Set last_seen_at = now."""

    def delete(self, key_id: int) -> HostKeyRecord:
        """Raises ``NotFound``. Returns the deleted record."""


class DismissedSuggestions(Protocol):
    def list_all(self) -> set[tuple[str, int]]: ...

    def dismiss(self, host: str, port: int) -> None:
        """Idempotent."""

    def restore(self, host: str, port: int) -> None:
        """Idempotent (undo of dismiss)."""


class SettingsRepo(Protocol):
    def get(self) -> AppSettings:
        """Stored values over ``AppSettings`` defaults."""

    def update(self, changes: Mapping[str, Any]) -> AppSettings:
        """Store only the given keys (already validated by the API schema)."""


class EventLog(Protocol):
    def record(self, kind: EventKind, connection_id: str | None = None, **detail: Any) -> None:
        """Append an audit event. ``detail`` may hold host, user, fingerprints and outcome
        codes only. The table keeps the newest 5,000 rows."""

    def recent(self, limit: int = 100) -> list[EventRecord]: ...


class KeysMetaRepo(Protocol):
    def all(self) -> dict[str, KeysMetaRecord]:
        """By fingerprint."""

    def upsert_seen(self, fp: str, last_path: str, source: KeySource) -> None:
        """Record where a key was last found; creates the row on first sight."""

    def set_label(self, fp: str, label: str | None) -> None: ...

    def set_hidden(self, fp: str, hidden: bool) -> None: ...


# --- keys (package 3) --------------------------------------------------------------------------


class KnownHostsReader(Protocol):
    """Read-only view of /ssh/known_hosts (re-read when the file's mtime changes)."""

    def lookup(self, host: str, port: int) -> KnownHostsMatch:
        """Entries matching host:port, including hashed and ``[host]:port`` entries."""

    def suggestions(self) -> SuggestionScan:
        """Plain-text, non-wildcard entries merged by shared key; hashed ones only counted."""


class KeyVault(Protocol):
    """Decrypted private keys held in memory only, with a TTL. Thread-safe."""

    def get(self, fp: str) -> asyncssh.SSHKey | None:
        """The unlocked key, or None if absent or expired."""

    def put(self, fp: str, key: asyncssh.SSHKey, ttl: float | None) -> None:
        """Hold ``key`` for ``ttl`` seconds (None = until ``lock_all``)."""

    def lock_all(self) -> int:
        """Drop every key. Returns how many were held."""

    def unlocked(self) -> list[str]:
        """Fingerprints currently held."""


class KeyProvider(Protocol):
    """Key discovery over /ssh and /keys-generated, deduplicated by fingerprint."""

    def list_keys(self) -> list[KeyRecord]:
        """Scans on every call. Includes hidden keys (``hidden=True``)."""

    def problems(self) -> list[KeyProblem]:
        """Files that look like keys but cannot be used (.ppk, unreadable, ...)."""

    def get(self, fp: str) -> KeyRecord:
        """Raises ``NotFound(key_unknown)``."""

    def default_key(self) -> KeyRecord | None:
        """Settings' default_key_fp if present and not hidden; otherwise the first visible key
        by preference id_ed25519, id_ecdsa, id_rsa, then name."""

    def resolve(self, key_fp: str | None) -> KeyRecord:
        """``key_fp`` or, when None, the default key. Raises ``NotFound(key_unknown)``."""

    def load_private(self, fp: str, passphrase: str | None = None) -> asyncssh.SSHKey:
        """From the vault if unlocked, else from disk. Raises ``KeyLocked`` when encrypted and
        no passphrase is given, ``BadRequest(bad_passphrase)`` on a wrong one,
        ``KeyUnreadable`` when the file is gone or invalid. Blocking: call via a thread."""

    def generate(self, spec: KeyGenSpec) -> KeyRecord:
        """Write ``<name>`` and ``<name>.pub`` into /keys-generated with O_EXCL plus atomic
        rename, LF line endings. Raises ``Conflict(key_name_taken)``,
        ``Invalid(key_name_invalid)``. Blocking (RSA generation): call via a thread."""

    def name_available(self, name: str) -> tuple[bool, str | None]:
        """(available, reason) where reason is "taken" or "invalid"."""


# --- security (package 1) ----------------------------------------------------------------------


class TokenStore(Protocol):
    """Bearer tokens; only SHA-256 hashes are stored. 30-day sliding expiry, never past an
    absolute lifetime of 90 days from issue (``Limits.token_max_age``)."""

    def issue(self) -> IssuedToken: ...

    def verify(self, token: str) -> bool:
        """True if valid; extends expiry (sliding, capped by the absolute lifetime). Never
        raises for a bad token."""

    def is_valid(self, token: str) -> bool:
        """True if valid, without extending the expiry (open WebSockets re-check with it)."""

    def revoke(self, token: str) -> None: ...

    def revoke_all_except(self, token: str) -> int: ...


class AppAuth(Protocol):
    def is_setup(self) -> bool:
        """True once an app password exists."""

    def new_setup_code(self) -> str | None:
        """Called once at app start. While no app password exists, make a fresh one-time setup
        code, hold it in memory only and return it so the caller prints it once (the startup
        log is the only place it may appear); otherwise drop any code and return None."""

    def setup_code_state(self) -> SetupCodeState | None:
        """None once a password exists; else "required" (a code is held) or "restart" (the
        password was cleared while the app ran, so no code is held until a restart)."""

    def setup(self, password: str, code: str, client: str) -> IssuedToken:
        """Set the first password. ``code`` is checked in constant time, in any case, with or
        without separators; wrong codes count against ``client``'s login backoff. The code is
        dropped once the password is set. Raises ``Conflict(already_setup)``,
        ``RateLimited``, ``Conflict(setup_needs_restart)``, ``Forbidden(bad_setup_code)``."""

    def login(self, password: str, client: str) -> IssuedToken:
        """``client`` is the peer address used for backoff. Raises ``Unauthorized
        (bad_password)``, ``RateLimited``, ``Conflict(not_setup)``."""

    def change_password(self, token: str, old: str, new: str) -> None:
        """Raises ``Unauthorized(bad_password)``, or ``RateLimited`` after repeated wrong old
        passwords (they share one backoff bucket, so a stolen token cannot brute-force the
        password). Revokes every token except ``token``."""

    def authenticate(self, authorization: str | None) -> str:
        """Validate an ``Authorization: Bearer`` header value; returns the token. Raises
        ``Unauthorized``."""


# --- SSH (package 4) ---------------------------------------------------------------------------


class Prompter(Protocol):
    """Asks the browser during connect. Implemented by ``api.ws_common.WsPrompter``.

    Every ask raises ``PromptCancelled`` on ``{"cancel": true}`` and ``PromptTimeout`` after
    120 s. Only one prompt is outstanding at a time. Replies are never logged."""

    async def status(self, phase: str, hop: HopRef, **detail: Any) -> None:
        """One-way progress (phase: connecting, hostkey, auth, ready)."""

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool: ...

    async def hostkey_changed(
        self,
        hop: HopRef,
        key_type: str,
        old_fingerprint: str,
        new_fingerprint: str,
        first_seen_at: datetime | None,
        *,
        old_key_type: str | None = None,
    ) -> tuple[bool, str]:
        """Returns (accept, confirm_name) unvalidated; the caller compares the name.
        ``old_key_type`` (added in package 4) is the stored key's type for the prompt's
        ``old_key_type``; None means the same as ``key_type``. Every prompter takes it."""

    async def passphrase(self, hop: HopRef, key: KeyRecord, retry: bool) -> tuple[str, bool]:
        """Returns (passphrase, remember)."""

    async def password(self, hop: HopRef, user: str, retry: bool) -> str: ...

    async def kbdint(
        self,
        hop: HopRef,
        name: str,
        instructions: str,
        prompts: list[tuple[str, bool]],
        *,
        retry: bool = False,
    ) -> list[str]:
        """``prompts`` is [(text, echo)]. Returns one answer per prompt. ``retry`` (added in
        package 4) is True on the first round of a new attempt after a failed one; every
        prompter takes it."""


class SshChain(Protocol):
    """An open chain: every hop's connection, target last."""

    @property
    def target(self) -> asyncssh.SSHClientConnection: ...

    @property
    def hops(self) -> list[HopRef]: ...

    async def close(self) -> None:
        """Close every hop, target first. Idempotent."""


class SshConnector(Protocol):
    async def connect(self, hops: list[HopTarget], prompter: Prompter) -> SshChain:
        """Connect hop by hop (each through the previous with ``tunnel=``), running the host
        key check and authentication of each. Raises ``SshError`` subclasses (``JumpFailed``
        wraps a jump hop's failure), ``PromptCancelled``, ``PromptTimeout``."""

    def plan(self, conn_id: str, force_password: bool = False) -> list[HopTarget]:
        """Hop targets for a saved connection (resolves the jump chain and default key).
        ``force_password`` makes the target use password/keyboard-interactive only. Raises
        ``UnknownConnection``."""

    def plan_new(self, jump_id: str | None, target: HopTarget) -> list[HopTarget]:
        """For the wizard: the saved chain of ``jump_id`` (if any) followed by an unsaved
        ``target``. Every ``HopRef.index``/``count`` is recomputed."""


# --- sessions (package 5) ----------------------------------------------------------------------


class SessionRegistry(Protocol):
    def list_all(self) -> list[SessionSummary]: ...

    async def kill(self, session_id: str) -> None:
        """Close the shell and every hop. Raises ``NotFound(session_gone)``."""

    async def start(self) -> None:
        """Start background work (detach-grace reaper). Called once at app startup."""

    async def aclose(self) -> None:
        """Close every session. Called once at app shutdown."""
