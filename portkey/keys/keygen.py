"""Key generation (package 3).

``asyncssh.generate_private_key`` plus ``export_private_key("openssh", passphrase)``; files go
to /keys-generated with ``O_CREAT | O_EXCL`` to a temporary name and an atomic rename, LF line
endings, never overwriting and never deleting. Default comment: ``<user>@<host> port-key
<date>`` is built by the frontend, not here.

Create-new without overwriting: the final names ``<name>`` and ``<name>.pub`` are first
claimed with ``O_EXCL`` (an empty placeholder), the content is written to a hidden temporary
file, also ``O_EXCL``, and renamed over the placeholder. A name that already exists fails the
claim, so nothing that existed before is ever replaced; readers never see a half-written key.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any

import asyncssh

from portkey.errors import Conflict, ErrorCode, Invalid, PortKeyError
from portkey.models import KeyGenSpec, KeyType

log = logging.getLogger("portkey.keys")

KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
# Names OpenSSH gives a meaning inside a .ssh folder; refused so a key never looks like one.
RESERVED_NAMES = frozenset(
    {"known_hosts", "known_hosts.old", "authorized_keys", "authorized_keys2", "config", "rc"}
)
DEFAULT_BITS: dict[KeyType, int | None] = {"ed25519": None, "rsa": 4096, "ecdsa": 256}
ALLOWED_BITS: dict[KeyType, frozenset[int | None]] = {
    "ed25519": frozenset({None}),
    "rsa": frozenset({2048, 3072, 4096}),
    "ecdsa": frozenset({256, 384, 521}),
}
CIPHER = "aes256-ctr"  # what ssh-keygen uses
KDF_ROUNDS = 64  # bcrypt-pbkdf rounds for a passphrase (ssh-keygen's default is 16)

_O_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def validate_key_name(name: str) -> bool:
    """True if ``name`` is an allowed file name (no path separators, no ``.pub``/``.ppk``)."""
    return (
        bool(KEY_NAME_RE.match(name))
        and not name.lower().endswith((".pub", ".ppk"))
        and name.lower() not in RESERVED_NAMES
    )


def name_taken(name: str, dest_dir: Path) -> bool:
    """True if ``<name>`` or ``<name>.pub`` exists in ``dest_dir``, ignoring case (the folder
    is on a Windows file system, where ``ID_RSA`` and ``id_rsa`` are the same file)."""
    wanted = {name.lower(), f"{name.lower()}.pub"}
    try:
        with os.scandir(dest_dir) as entries:
            return any(entry.name.lower() in wanted for entry in entries)
    except FileNotFoundError:
        return False


def algorithm_for(key_type: KeyType, bits: int | None) -> tuple[str, dict[str, Any], int]:
    """(asyncssh algorithm name, generate kwargs, effective bits)."""
    size = DEFAULT_BITS[key_type] if bits is None else bits
    if size not in ALLOWED_BITS[key_type]:
        raise Invalid(f"{bits} bits is not valid for {key_type} keys.", code=ErrorCode.VALIDATION)
    if key_type == "ed25519":
        return "ssh-ed25519", {}, 256
    if key_type == "rsa":
        assert size is not None
        return "ssh-rsa", {"key_size": size}, size
    return f"ecdsa-sha2-nistp{size}", {}, int(size or 256)


def generate_key_files(spec: KeyGenSpec, dest_dir: Path) -> Path:
    """Write ``<name>`` and ``<name>.pub`` into ``dest_dir`` and return the private key path.
    Raises ``Conflict(key_name_taken)`` if either file exists, ``Invalid(key_name_invalid)``.
    Blocking."""
    if not validate_key_name(spec.name):
        raise Invalid(
            "Use letters, digits, '.', '_' and '-' only (at most 64), not ending in .pub or .ppk.",
            code=ErrorCode.KEY_NAME_INVALID,
        )
    if name_taken(spec.name, dest_dir):
        raise _taken(spec.name)
    algorithm, kwargs, _ = algorithm_for(spec.type, spec.bits)
    key = asyncssh.generate_private_key(algorithm, comment=spec.comment or None, **kwargs)
    private_data = key.export_private_key(
        "openssh", spec.passphrase or None, cipher_name=CIPHER, rounds=KDF_ROUNDS
    )
    public_data = key.export_public_key("openssh")
    del key

    private_path = dest_dir / spec.name
    public_path = dest_dir / f"{spec.name}.pub"
    created: list[Path] = []
    try:
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        for path in (private_path, public_path):
            _claim(path)
            created.append(path)
        _write_over_claim(public_path, public_data, 0o644)
        _write_over_claim(private_path, private_data, 0o600)
    except FileExistsError:
        _remove_own(created)
        raise _taken(spec.name) from None
    except OSError as exc:
        _remove_own(created)
        log.warning("could not write a generated key (%s)", type(exc).__name__)
        raise PortKeyError("Port-Key couldn't write to the generated-keys folder.") from None
    return private_path


def _taken(name: str) -> Conflict:
    return Conflict(
        f"A key named {name} already exists. Choose another name.",
        code=ErrorCode.KEY_NAME_TAKEN,
    )


def _claim(path: Path) -> None:
    """Create ``path`` empty, failing with ``FileExistsError`` if it exists in any form."""
    os.close(os.open(path, _O_FLAGS, 0o600))


def _write_over_claim(final: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to a new hidden temp file, then atomically rename it over the empty
    placeholder this call claimed at ``final``."""
    tmp = final.with_name(f".{final.name}.tmp-{secrets.token_hex(6)}")
    fd = os.open(tmp, _O_FLAGS, mode)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except OSError:
        os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.close(fd)
    try:
        os.replace(tmp, final)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _remove_own(paths: list[Path]) -> None:
    """Remove files this call created (placeholders or a half-finished pair)."""
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()
