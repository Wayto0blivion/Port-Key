"""Key discovery and the key provider (package 3). Implements ``interfaces.KeyProvider``.

Scans ``config.ssh_dir`` (top level and ``port-key/``) and ``config.keys_dir`` on every
``list_keys`` call, reads files with ``textio``, deduplicates by fingerprint (prefer the
/keys-generated copy's provenance ``generated``), merges ``keys_meta`` labels and hidden flags
(updating ``last_path``), and maps container paths to the display paths in ``config``. The
public key comes from ``<name>.pub`` or, if missing, from the cleartext public block of an
OpenSSH-format private key, so encrypted keys list without a passphrase. ``.ppk`` files,
non-keys and unreadable files are reported by ``problems()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from portkey.config import Config
from portkey.interfaces import KeysMetaRepo, KeyVault, SettingsRepo
from portkey.models import KeyGenSpec, KeyProblem, KeyRecord

if TYPE_CHECKING:
    import asyncssh


class FsKeyProvider:
    def __init__(
        self, config: Config, meta: KeysMetaRepo, settings: SettingsRepo, vault: KeyVault
    ) -> None:
        self.config = config
        self.meta = meta
        self.settings = settings
        self.vault = vault

    def list_keys(self) -> list[KeyRecord]:
        raise NotImplementedError("package 3")

    def problems(self) -> list[KeyProblem]:
        raise NotImplementedError("package 3")

    def get(self, fp: str) -> KeyRecord:
        raise NotImplementedError("package 3")

    def default_key(self) -> KeyRecord | None:
        raise NotImplementedError("package 3")

    def resolve(self, key_fp: str | None) -> KeyRecord:
        raise NotImplementedError("package 3")

    def load_private(self, fp: str, passphrase: str | None = None) -> asyncssh.SSHKey:
        raise NotImplementedError("package 3")

    def generate(self, spec: KeyGenSpec) -> KeyRecord:
        raise NotImplementedError("package 3")

    def name_available(self, name: str) -> tuple[bool, str | None]:
        raise NotImplementedError("package 3")
