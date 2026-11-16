"""Runtime configuration: paths, ports and limits.

Everything comes from environment variables with the container defaults. ``load_config()`` is
called once by the app factory; tests build a ``Config`` directly or pass a custom ``env``.

Environment variables
    PORTKEY_DATA_DIR           /data            SQLite database lives here (named volume)
    PORTKEY_SSH_DIR            /ssh             the owner's .ssh folder, mounted read-only
    PORTKEY_KEYS_DIR           /keys-generated  generated keys (create-only)
    PORTKEY_SSH_DIR_DISPLAY    (PORTKEY_SSH_DIR)   host path of the .ssh folder, shown in the UI
    PORTKEY_KEYS_DIR_DISPLAY   (<display>\\port-key) host path of the generated-keys folder
    PORTKEY_LISTEN_HOST        0.0.0.0
    PORTKEY_LISTEN_PORT        8000             port uvicorn binds inside the container
    PORTKEY_PUBLIC_PORT        8822             port the browser uses (Host/Origin allowlist)
    PORTKEY_ALLOWED_HOSTS      (derived)        comma-separated host:port list; replaces the
                                                default localhost/127.0.0.1/[::1] list (tests)
    PORTKEY_LOG_LEVEL          info
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from portkey import __version__


@dataclass(frozen=True, slots=True)
class Limits:
    """Fixed limits from docs/architecture.md. Values are in bytes, seconds or counts."""

    max_sessions: int = 32
    ring_buffer_bytes: int = 256 * 1024
    ws_max_frame_bytes: int = 64 * 1024
    max_body_bytes: int = 256 * 1024  # HTTP request bodies; the biggest real one is ~40 KB
    graceful_shutdown_seconds: int = 5  # uvicorn's wait for open sockets on shutdown
    ws_auth_timeout: float = 5.0
    ws_token_recheck: float = 10.0  # open sockets re-check their token this often
    ws_ping_interval: float = 20.0
    ws_ping_timeout: float = 20.0
    prompt_timeout: float = 120.0
    wizard_idle_timeout: float = 600.0
    output_batch_seconds: float = 0.016
    output_batch_bytes: int = 64 * 1024
    ssh_connect_timeout: float = 10.0
    ssh_keepalive_interval: float = 30.0
    ssh_keepalive_count_max: int = 3
    max_jump_hops: int = 3
    max_group_depth: int = 3
    max_auth_attempts: int = 3
    terminal_min_size: int = 1
    terminal_max_size: int = 1000
    token_ttl: float = 30 * 24 * 3600.0  # sliding: each use moves expiry this far ahead
    token_max_age: float = 90 * 24 * 3600.0  # absolute: no token outlives its creation by more
    events_cap: int = 5000
    reachability_timeout: float = 2.0
    reachability_max_ids: int = 500
    bulk_max_ids: int = 1000
    # Defaults for values the owner can change in Settings.
    default_detach_grace: int = 15 * 60
    default_passphrase_ttl: int = 8 * 3600
    unlock_no_remember_ttl: int = 60


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path = Path("/data")
    ssh_dir: Path = Path("/ssh")
    keys_dir: Path = Path("/keys-generated")
    ssh_dir_display: str = "/ssh"
    keys_dir_display: str = "/keys-generated"
    listen_host: str = "0.0.0.0"  # noqa: S104 - inside the container; published on 127.0.0.1 only
    listen_port: int = 8000
    public_port: int = 8822
    allowed_hosts: tuple[str, ...] = ()
    log_level: str = "info"
    version: str = __version__
    limits: Limits = field(default_factory=Limits)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portkey.db"

    @property
    def known_hosts_path(self) -> Path:
        return self.ssh_dir / "known_hosts"

    @property
    def static_dir(self) -> Path:
        return Path(__file__).parent / "static"

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Exact Origin values accepted on WebSockets and non-GET requests."""
        return tuple(f"http://{h}" for h in self.allowed_hosts)


def default_allowed_hosts(public_port: int) -> tuple[str, ...]:
    return (f"localhost:{public_port}", f"127.0.0.1:{public_port}", f"[::1]:{public_port}")


_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]|\\")


def to_display_path(path: str) -> str:
    """Normalise a host path for display: Windows-looking paths get backslashes only."""
    if _WINDOWS_PATH.search(path):
        return path.replace("/", "\\")
    return path


def join_display(base: str, *parts: str) -> str:
    """Join display-path segments with the separator the base already uses."""
    sep = "\\" if _WINDOWS_PATH.search(base) else "/"
    out = base.rstrip("\\/")
    for part in parts:
        out = f"{out}{sep}{part.strip('/')}"
    return out


def load_config(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    data_dir = Path(e.get("PORTKEY_DATA_DIR", "/data"))
    ssh_dir = Path(e.get("PORTKEY_SSH_DIR", "/ssh"))
    keys_dir = Path(e.get("PORTKEY_KEYS_DIR", "/keys-generated"))
    ssh_display = to_display_path(e.get("PORTKEY_SSH_DIR_DISPLAY") or str(ssh_dir))
    keys_display = e.get("PORTKEY_KEYS_DIR_DISPLAY")
    keys_display = (
        to_display_path(keys_display) if keys_display else join_display(ssh_display, "port-key")
    )
    public_port = int(e.get("PORTKEY_PUBLIC_PORT", "8822"))
    hosts_env = e.get("PORTKEY_ALLOWED_HOSTS", "").strip()
    allowed = (
        tuple(h.strip().lower() for h in hosts_env.split(",") if h.strip())
        if hosts_env
        else default_allowed_hosts(public_port)
    )
    return Config(
        data_dir=data_dir,
        ssh_dir=ssh_dir,
        keys_dir=keys_dir,
        ssh_dir_display=ssh_display,
        keys_dir_display=keys_display,
        listen_host=e.get("PORTKEY_LISTEN_HOST", "0.0.0.0"),  # noqa: S104
        listen_port=int(e.get("PORTKEY_LISTEN_PORT", "8000")),
        public_port=public_port,
        allowed_hosts=allowed,
        log_level=e.get("PORTKEY_LOG_LEVEL", "info").lower(),
    )
