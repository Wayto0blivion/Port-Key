"""Fixtures for package 3 (keys, known_hosts, key and host routes).

Every key is generated inside the test run; no key material is committed. In-memory fakes stand
in for package 2's repositories so these tests do not depend on the store being finished.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import pytest

from portkey.config import Config
from portkey.errors import NotFound
from portkey.keys._wire import parse_public_line
from portkey.keys.discovery import FsKeyProvider
from portkey.keys.vault import MemoryKeyVault
from portkey.models import (
    AppSettings,
    ConnectionRecord,
    ConnectionRef,
    HostKeyRecord,
    KeysMetaRecord,
    KeySource,
)
from portkey.services import Services

NOW = datetime(2026, 11, 15, 14, 2, tzinfo=UTC)
FIXTURE_ROUNDS = 50  # bcrypt rounds for test keys: fast, and above bcrypt's warning threshold
PASSPHRASE = "correct horse battery"  # a test-only passphrase


# --- key files ----------------------------------------------------------------------------------


def encode(text: str, encoding: str) -> bytes:
    """Write text the way Windows tools might: 'lf', 'crlf', 'bom' (UTF-8 BOM + CRLF),
    'utf16' (PowerShell 5: UTF-16 LE BOM + CRLF), 'utf16be'."""
    if encoding == "lf":
        return text.encode()
    crlf = text.replace("\n", "\r\n")
    if encoding == "crlf":
        return crlf.encode()
    if encoding == "bom":
        return b"\xef\xbb\xbf" + crlf.encode()
    if encoding == "utf16":
        return b"\xff\xfe" + crlf.encode("utf-16-le")
    if encoding == "utf16be":
        return b"\xfe\xff" + crlf.encode("utf-16-be")
    raise ValueError(encoding)


@dataclass
class KeyFiles:
    key: asyncssh.SSHKey
    private: Path
    public: Path | None

    @property
    def fp(self) -> str:
        return str(self.key.get_fingerprint("sha256"))

    @property
    def public_line(self) -> str:
        return bytes(self.key.export_public_key("openssh")).decode().strip()


def write_key(
    directory: Path,
    name: str,
    alg: str = "ssh-ed25519",
    fmt: str = "openssh",
    passphrase: str | None = None,
    *,
    pub: bool = True,
    comment: str | None = "john@build-box",
    encoding: str = "lf",
    key: asyncssh.SSHKey | None = None,
    **kwargs: Any,
) -> KeyFiles:
    """Generate (or reuse) a key and write it in ``fmt`` ('openssh', 'pkcs1-pem', 'pkcs8-pem')."""
    key = key or asyncssh.generate_private_key(alg, comment=comment, **kwargs)
    extra: dict[str, Any] = {"rounds": FIXTURE_ROUNDS} if fmt == "openssh" and passphrase else {}
    data = bytes(key.export_private_key(fmt, passphrase, **extra)).decode()
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / name
    private.write_bytes(encode(data, encoding))
    public = None
    if pub:
        public = directory / f"{name}.pub"
        public.write_bytes(encode(bytes(key.export_public_key("openssh")).decode(), encoding))
    return KeyFiles(key, private, public)


# --- fakes for package 2 ----------------------------------------------------------------------


class FakeKeysMeta:
    def __init__(self) -> None:
        self.rows: dict[str, KeysMetaRecord] = {}
        self.upserts = 0

    def all(self) -> dict[str, KeysMetaRecord]:
        return dict(self.rows)

    def upsert_seen(self, fp: str, last_path: str, source: KeySource) -> None:
        self.upserts += 1
        old = self.rows.get(fp)
        if old is None:
            self.rows[fp] = KeysMetaRecord(fp, None, last_path, source, False, NOW)
        else:
            self.rows[fp] = replace(old, last_path=last_path, source=source)

    def _ensure(self, fp: str) -> KeysMetaRecord:
        return self.rows.setdefault(fp, KeysMetaRecord(fp, None, "", "ssh_dir", False, NOW))

    def set_label(self, fp: str, label: str | None) -> None:
        self.rows[fp] = replace(self._ensure(fp), label=label)

    def set_hidden(self, fp: str, hidden: bool) -> None:
        self.rows[fp] = replace(self._ensure(fp), hidden=hidden)


class FakeSettings:
    def __init__(self, **values: Any) -> None:
        self.value = AppSettings(**values)

    def get(self) -> AppSettings:
        return self.value

    def update(self, changes: Mapping[str, Any]) -> AppSettings:
        self.value = replace(self.value, **dict(changes))
        return self.value


def make_conn(
    name: str,
    host: str = "192.168.10.42",
    port: int = 22,
    *,
    auth_method: str = "key",
    key_fp: str | None = None,
) -> ConnectionRecord:
    return ConnectionRecord(
        id=f"00000000-0000-4000-8000-{abs(hash(name)) % 10**12:012d}",
        name=name,
        host=host,
        port=port,
        username="john",
        auth_method=auth_method,  # type: ignore[arg-type]
        key_fp=key_fp,
        jump_id=None,
        group_id=None,
        tags=(),
        color="grey",
        pinned=False,
        notes="",
        sort_order=0,
        created_at=NOW,
        updated_at=NOW,
        last_used_at=None,
        use_count=0,
    )


class FakeConnections:
    def __init__(self, *conns: ConnectionRecord) -> None:
        self.conns = list(conns)

    def list_all(self) -> list[ConnectionRecord]:
        return list(self.conns)

    def using_key(self, fp: str, default_fp: str | None) -> list[ConnectionRef]:
        return [
            ConnectionRef(c.id, c.name)
            for c in self.conns
            if c.auth_method == "key"
            and (
                c.key_fp == fp or (c.key_fp is None and default_fp is not None and fp == default_fp)
            )
        ]

    def reassign_key(
        self, fp: str, default_fp: str | None, auth_method: str, key_fp: str | None
    ) -> list[ConnectionRef]:
        changed = self.using_key(fp, default_fp)
        ids = {c.id for c in changed}
        self.conns = [
            replace(c, auth_method=auth_method, key_fp=key_fp) if c.id in ids else c  # type: ignore[arg-type]
            for c in self.conns
        ]
        return changed


class FakeDismissed:
    def __init__(self) -> None:
        self.items: set[tuple[str, int]] = set()

    def list_all(self) -> set[tuple[str, int]]:
        return set(self.items)

    def dismiss(self, host: str, port: int) -> None:
        self.items.add((host, port))

    def restore(self, host: str, port: int) -> None:
        self.items.discard((host, port))


class FakeHostKeys:
    def __init__(self, *records: HostKeyRecord) -> None:
        self.records = {r.id: r for r in records}

    def list_all(self) -> list[HostKeyRecord]:
        return list(self.records.values())

    def get(self, key_id: int) -> HostKeyRecord:
        if key_id not in self.records:
            raise NotFound()
        return self.records[key_id]

    def delete(self, key_id: int) -> HostKeyRecord:
        return self.records.pop(self.get(key_id).id)


@dataclass
class FakeEvents:
    recorded: list[tuple[str, str | None, dict[str, Any]]] = field(default_factory=list)

    def record(self, kind: str, connection_id: str | None = None, **detail: Any) -> None:
        self.recorded.append((kind, connection_id, detail))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def meta() -> FakeKeysMeta:
    return FakeKeysMeta()


@pytest.fixture
def settings() -> FakeSettings:
    return FakeSettings()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def vault(clock: FakeClock) -> MemoryKeyVault:
    return MemoryKeyVault(clock=clock)


@pytest.fixture
def provider(
    config: Config, meta: FakeKeysMeta, settings: FakeSettings, vault: MemoryKeyVault
) -> FsKeyProvider:
    return FsKeyProvider(config, meta, settings, vault)


@pytest.fixture
def ssh_dir(config: Config) -> Path:
    return config.ssh_dir


@pytest.fixture
def keys_dir(config: Config) -> Path:
    return config.keys_dir


@dataclass
class Wired:
    services: Services
    meta: FakeKeysMeta
    settings: FakeSettings
    connections: FakeConnections
    dismissed: FakeDismissed
    host_keys: FakeHostKeys
    events: FakeEvents
    clock: FakeClock


@pytest.fixture
def wired(
    services: Services,
    meta: FakeKeysMeta,
    settings: FakeSettings,
    vault: MemoryKeyVault,
    clock: FakeClock,
) -> Wired:
    """Replace package 2's stores in ``services`` with fakes; keep package 3's real classes."""
    w = Wired(
        services,
        meta,
        settings,
        FakeConnections(),
        FakeDismissed(),
        FakeHostKeys(),
        FakeEvents(),
        clock,
    )
    services.keys_meta = meta
    services.settings = settings
    services.connections = w.connections  # type: ignore[assignment]
    services.dismissed = w.dismissed
    services.host_keys = w.host_keys  # type: ignore[assignment]
    services.events = w.events  # type: ignore[assignment]
    services.vault = vault
    services.keys = FsKeyProvider(services.config, meta, settings, vault)
    return w


@pytest.fixture
def api(authed_client: httpx.AsyncClient, wired: Wired) -> httpx.AsyncClient:
    """``authed_client`` (auth overridden) over an app whose package 2 stores are fakes."""
    return authed_client


def public_of(line: str) -> str:
    """``"<type> <base64>"`` of a public key line (comment dropped)."""
    return parse_public_line(line)[0].openssh


def chmod_all(path: Path) -> None:
    """Make every file look like a Windows bind mount (mode 0777)."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.chmod(os.path.join(root, name), 0o777)


@dataclass(frozen=True)
class Kit:
    """Helpers for test modules (``--import-mode=importlib`` cannot import conftest)."""

    write_key: Callable[..., KeyFiles] = write_key
    encode: Callable[[str, str], bytes] = encode
    make_conn: Callable[..., ConnectionRecord] = make_conn
    public_of: Callable[[str], str] = public_of
    chmod_all: Callable[[Path], None] = chmod_all
    passphrase: str = PASSPHRASE
    now: datetime = NOW


@pytest.fixture
def kit() -> Kit:
    return Kit()
