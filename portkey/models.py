"""Domain records shared between packages (frozen contract).

Plain frozen dataclasses with no I/O. Repositories return them; routes convert them to API
schemas with ``Schema.model_validate(record, from_attributes=True)`` because field names match.
All datetimes are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

AuthMethod = Literal["key", "password"]
Color = Literal["red", "amber", "green", "blue", "purple", "grey"]
KeyType = Literal["ed25519", "rsa", "ecdsa"]
KeySource = Literal["ssh_dir", "generated"]
KeyProblemReason = Literal["ppk", "not_private_key", "unreadable", "unsupported_type"]
HopRole = Literal["jump", "target"]
# While no app password exists: "required" (a setup code is held; it is in the startup log) or
# "restart" (none is held because the password was cleared while the app ran).
SetupCodeState = Literal["required", "restart"]
EventKind = Literal[
    "hostkey_accepted",
    "hostkey_replaced",
    "hostkey_forgotten",
    "key_installed",
    "key_generated",
    "session_opened",
    "session_closed",
    "password_changed",
]

COLORS: tuple[Color, ...] = ("red", "amber", "green", "blue", "purple", "grey")
TAG_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,31}$"


# --- connections, folders, tags ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    id: str
    name: str
    host: str
    port: int
    username: str
    auth_method: AuthMethod
    key_fp: str | None  # None means "the default key"
    jump_id: str | None
    group_id: str | None  # None means ungrouped
    tags: tuple[str, ...]  # lower-case, sorted
    color: Color
    pinned: bool
    notes: str
    sort_order: int
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    use_count: int


@dataclass(frozen=True, slots=True)
class ConnectionDraft:
    """Fields for creating a connection. Validation happens in the repository."""

    name: str
    host: str
    username: str
    auth_method: AuthMethod
    port: int = 22
    key_fp: str | None = None
    jump_id: str | None = None
    group_id: str | None = None
    tags: tuple[str, ...] = ()
    color: Color = "grey"
    pinned: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionRef:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class GroupRecord:
    """A folder. ``connection_count`` includes connections in descendant folders."""

    id: str
    name: str
    parent_id: str | None
    sort_order: int
    created_at: datetime
    connection_count: int = 0


@dataclass(frozen=True, slots=True)
class TagCount:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class BulkChange:
    """One atomic bulk edit. ``set_group`` False means "leave folders alone"."""

    ids: tuple[str, ...]
    set_group: bool = False
    group_id: str | None = None
    add_tags: tuple[str, ...] = ()
    remove_tags: tuple[str, ...] = ()


# --- host keys and known_hosts ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HostKeyRecord:
    """A host key trusted in the app database (never the owner's known_hosts)."""

    id: int
    host: str  # lower-case
    port: int
    key_type: str  # OpenSSH algorithm name, e.g. "ssh-ed25519"
    public_key: str  # "ssh-ed25519 AAAA..." without comment
    fp_sha256: str  # "SHA256:..."
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class KnownHostsMatch:
    """What the owner's known_hosts says about one host:port. Keys are OpenSSH public-key text."""

    trusted: tuple[str, ...] = ()
    revoked: tuple[str, ...] = ()
    cert_authorities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Suggestion:
    """known_hosts entries that share a key, merged. ``host`` is the preferred (non-IP) name."""

    host: str
    port: int
    aliases: tuple[str, ...]
    key_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuggestionScan:
    suggestions: tuple[Suggestion, ...]
    hashed_count: int


# --- keys ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeyRecord:
    """A usable private key found on disk, deduplicated by fingerprint."""

    fp: str  # "SHA256:<43 base64 chars>"
    name: str  # file name, e.g. "id_ed25519"
    type: KeyType
    bits: int
    comment: str
    path: Path  # container path of the private key
    display_path: str  # host (Windows) path for the UI
    source: KeySource
    encrypted: bool
    public_key: str  # "ssh-ed25519 AAAA... comment"
    label: str | None = None
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class KeyProblem:
    display_path: str
    reason: KeyProblemReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class KeyGenSpec:
    name: str
    type: KeyType
    bits: int | None
    comment: str
    passphrase: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class KeysMetaRecord:
    fp: str
    label: str | None
    last_path: str
    source: KeySource
    hidden: bool
    created_at: datetime


# --- settings, events, auth ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppSettings:
    default_key_fp: str | None = None
    default_user: str = ""
    passphrase_ttl: int = 8 * 3600
    detach_grace: int = 15 * 60
    font_size: int = 14
    windows_copy_paste: bool = True
    leave_warning: bool = True


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: int
    ts: datetime
    kind: EventKind
    connection_id: str | None
    detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str = field(repr=False)
    expires_at: datetime


# --- SSH and sessions ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HopRef:
    """Which hop of a chain a status, prompt or error refers to.

    ``index`` counts from 0 at the outermost jump host; the target has ``index == count - 1``.
    ``connection_id`` is None for a wizard target that is not saved yet.
    """

    index: int
    count: int
    connection_id: str | None
    name: str
    host: str
    port: int
    role: HopRole


@dataclass(frozen=True, slots=True)
class HopTarget:
    """Everything needed to connect and authenticate one hop."""

    ref: HopRef
    username: str
    auth_method: AuthMethod
    key_fp: str | None  # resolved: never None when auth_method == "key"
    allow_password: bool = True
    allow_kbdint: bool = True
    allow_key: bool = True


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    connection_id: str
    started_at: datetime
    attached: bool
    detached_at: datetime | None = None
