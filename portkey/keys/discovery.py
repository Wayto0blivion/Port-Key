"""Key discovery and the key provider (package 3). Implements ``interfaces.KeyProvider``.

Scans ``config.ssh_dir`` (top level and ``port-key/``) and ``config.keys_dir`` on every
``list_keys`` call, reads files with ``textio``, deduplicates by fingerprint (prefer the
/keys-generated copy's provenance ``generated``), merges ``keys_meta`` labels and hidden flags
(updating ``last_path``), and maps container paths to the display paths in ``config``. The
public key comes from ``<name>.pub`` or, if missing, from the cleartext public block of an
OpenSSH-format private key, so encrypted keys list without a passphrase. ``.ppk`` files,
non-keys and unreadable files are reported by ``problems()``.

Private key material is only decrypted in ``load_private_from_disk``; the result goes to the
caller (and from there to the vault), never to disk or a log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import asyncssh

from portkey.config import Config, join_display
from portkey.errors import BadRequest, ErrorCode, Invalid, NotFound
from portkey.interfaces import KeysMetaRepo, KeyVault, SettingsRepo
from portkey.keys._parse import ParseProblem, parse_private_key
from portkey.keys._scan import FoundKey, read_key_text, scan
from portkey.keys._wire import parse_public_blob
from portkey.keys.keygen import generate_key_files, name_taken, validate_key_name
from portkey.models import KeyGenSpec, KeyProblem, KeyRecord, KeysMetaRecord
from portkey.ssh.errors import KeyLocked, KeyUnreadable

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger("portkey.keys")

# Default-key preference when Settings names none (docs/api.md "Settings").
PREFERRED_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")


def key_unknown() -> NotFound:
    return NotFound("Port-Key can't find that key.", code=ErrorCode.KEY_UNKNOWN)


def load_private_from_disk(record: KeyRecord, passphrase: str | None) -> asyncssh.SSHKey:
    """Read and (if needed) decrypt ``record``'s file, bypassing the vault. Raises
    ``KeyLocked`` (encrypted, no passphrase), ``BadRequest(bad_passphrase)``, or
    ``KeyUnreadable`` (gone, damaged, or not the key that was listed). Blocking."""
    try:
        text = read_key_text(record.path)
    except OSError:
        text = None
    if text is None:
        raise KeyUnreadable(f"Port-Key can't read {record.display_path}.")
    if record.encrypted and not passphrase:
        raise KeyLocked(f"{record.name} is protected by a passphrase.")
    try:
        key = asyncssh.import_private_key(text, passphrase if record.encrypted else None)
    except (asyncssh.KeyEncryptionError, asyncssh.KeyImportError, ValueError):
        if record.encrypted:
            raise BadRequest(
                f"That passphrase doesn't unlock {record.name}. Try again.",
                code=ErrorCode.BAD_PASSPHRASE,
            ) from None
        raise KeyUnreadable(f"{record.display_path} is not a valid private key.") from None
    if parse_public_blob(bytes(key.public_data)).fingerprint != record.fp:
        raise KeyUnreadable(f"{record.display_path} does not match its public key.")
    if record.comment:
        key.set_comment(record.comment)
    return key


class FsKeyProvider:
    def __init__(
        self, config: Config, meta: KeysMetaRepo, settings: SettingsRepo, vault: KeyVault
    ) -> None:
        self.config = config
        self.meta = meta
        self.settings = settings
        self.vault = vault

    # --- listing ----------------------------------------------------------------------------

    def list_keys(self) -> list[KeyRecord]:
        found = scan(self.config).keys
        meta = self.meta.all()
        records = [self._record(f, meta.get(f.parsed.fp)) for f in found]
        return sorted(records, key=lambda r: (r.name.lower(), r.display_path))

    def problems(self) -> list[KeyProblem]:
        return scan(self.config).problems

    def get(self, fp: str) -> KeyRecord:
        for record in self.list_keys():
            if record.fp == fp:
                return record
        raise key_unknown()

    def default_key(self) -> KeyRecord | None:
        return pick_default(self.list_keys(), self.settings.get().default_key_fp)

    def resolve(self, key_fp: str | None) -> KeyRecord:
        if key_fp is not None:
            return self.get(key_fp)
        record = self.default_key()
        if record is None:
            raise NotFound("No SSH key found to use as the default.", code=ErrorCode.KEY_UNKNOWN)
        return record

    # --- private keys ---------------------------------------------------------------------

    def load_private(self, fp: str, passphrase: str | None = None) -> asyncssh.SSHKey:
        held = self.vault.get(fp)
        if held is not None:
            return held
        try:
            record = self.get(fp)
        except NotFound:
            raise KeyUnreadable("Port-Key can't find that key file any more.") from None
        return load_private_from_disk(record, passphrase)

    # --- generation -------------------------------------------------------------------------

    def generate(self, spec: KeyGenSpec) -> KeyRecord:
        path = generate_key_files(spec, self.config.keys_dir)
        display = join_display(self.config.keys_dir_display, spec.name)
        record = self._read_generated(path, display, spec.name)
        log.info("generated key %s (%s)", record.fp, record.type)
        return record

    def name_available(self, name: str) -> tuple[bool, str | None]:
        if not validate_key_name(name):
            return False, "invalid"
        if name_taken(name, self.config.keys_dir):
            return False, "taken"
        return True, None

    # --- helpers ----------------------------------------------------------------------------

    def _record(self, found: FoundKey, meta: KeysMetaRecord | None) -> KeyRecord:
        fp = found.parsed.fp
        if meta is None or meta.last_path != found.display_path or meta.source != found.source:
            self.meta.upsert_seen(fp, found.display_path, found.source)
        p = found.parsed
        return KeyRecord(
            fp=fp,
            name=found.name,
            type=p.type,
            bits=p.bits,
            comment=p.comment,
            path=found.path,
            display_path=found.display_path,
            source=found.source,
            encrypted=p.encrypted,
            public_key=p.public_key,
            label=meta.label if meta else None,
            hidden=meta.hidden if meta else False,
        )

    def _read_generated(self, path: Path, display: str, name: str) -> KeyRecord:
        try:
            text = read_key_text(path)
            pub_text = read_key_text(path.with_name(f"{name}.pub"))
        except OSError:
            text = pub_text = None
        try:
            parsed = parse_private_key(text, pub_text) if text else None
        except ParseProblem:
            parsed = None
        if parsed is None:
            raise Invalid("The new key could not be read back.", code=ErrorCode.KEY_UNREADABLE)
        found = FoundKey(parsed, name, path, display, "generated")
        return self._record(found, None)


def pick_default(records: list[KeyRecord], configured_fp: str | None) -> KeyRecord | None:
    """Settings' key if present and visible; else id_ed25519, id_ecdsa, id_rsa, then by name
    (keys from .ssh before generated ones of the same name)."""
    visible = [r for r in records if not r.hidden]
    if configured_fp:
        for record in visible:
            if record.fp == configured_fp:
                return record

    def rank(r: KeyRecord) -> tuple[int, str, int, str]:
        name = r.name.lower()
        pref = PREFERRED_NAMES.index(name) if name in PREFERRED_NAMES else len(PREFERRED_NAMES)
        return (pref, name, 0 if r.source == "ssh_dir" else 1, r.display_path)

    return min(visible, key=rank, default=None)
