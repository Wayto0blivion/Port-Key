"""Sessions, settings and diagnostics: /api/sessions*, /api/settings, /api/diag*."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from portkey.api.schemas.common import Fingerprint, Hostname, Port, Request, Response

# --- sessions -----------------------------------------------------------------------------------


class SessionOut(Response):
    session_id: str
    connection_id: str
    started_at: datetime
    attached: bool
    detached_at: datetime | None


class SessionList(Response):
    sessions: list[SessionOut]


# --- settings -----------------------------------------------------------------------------------


class About(Response):
    ssh_dir: str  # Windows path
    keys_dir: str  # Windows path
    data_dir: str  # container path of the named volume
    version: str


class SettingsOut(Response):
    default_key_fp: str | None
    default_user: str
    passphrase_ttl: int  # seconds
    detach_grace: int  # seconds
    font_size: int
    windows_copy_paste: bool
    leave_warning: bool
    about: About


class SettingsPatch(Request):
    """Any subset. ``default_key_fp: null`` clears it (first key by preference is used)."""

    default_key_fp: Fingerprint | None = None
    default_user: Annotated[str, Field(max_length=128, pattern=r"^\S*$")] | None = None
    passphrase_ttl: Annotated[int, Field(ge=60, le=7 * 86400)] | None = None
    detach_grace: Annotated[int, Field(ge=60, le=86400)] | None = None
    font_size: Annotated[int, Field(ge=8, le=32)] | None = None
    windows_copy_paste: bool | None = None
    leave_warning: bool | None = None


# --- diagnostics --------------------------------------------------------------------------------


class DirStatus(Response):
    path: str  # Windows path for display (data_dir: container path)
    present: bool
    readable: bool
    writable: bool


class SshDirStatus(DirStatus):
    key_count: int
    known_hosts: bool
    mode_note: str | None  # informational: bind-mounted files show as 0777


class KeyProblemDiag(Response):
    path: str
    reason: Literal["ppk", "not_private_key", "unreadable", "unsupported_type"]
    detail: str


class DiagReport(Response):
    version: str
    python: str
    asyncssh: str
    ssh_dir: SshDirStatus
    keys_dir: DirStatus
    data_dir: DirStatus
    key_problems: list[KeyProblemDiag]
    notes: list[str]  # e.g. the ssh-agent explanation when no key file was found


class ResolveRequest(Request):
    host: Hostname
    port: Port = 22


class TcpCheck(Response):
    state: Literal["up", "down", "skipped"]
    ms: int | None
    error: Literal["refused", "timeout", "unreachable"] | None


class ResolveResult(Response):
    host: str
    port: int
    addresses: list[str]
    resolve_error: str | None  # resolver message when the name did not resolve
    tcp: TcpCheck
