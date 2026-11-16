"""FsKeyProvider: keys_meta merge, default key, resolve, load_private and the vault."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from portkey.errors import BadRequest, NotFound
from portkey.keys.discovery import FsKeyProvider, load_private_from_disk
from portkey.ssh.errors import KeyLocked, KeyUnreadable


def test_meta_is_merged_and_only_written_on_change(
    provider: FsKeyProvider, ssh_dir: Path, meta: Any, kit: Any
) -> None:
    files = kit.write_key(ssh_dir, "id_ed25519")
    [key] = provider.list_keys()
    assert (key.label, key.hidden) == (None, False)
    assert meta.rows[files.fp].last_path == "C:\\Users\\john\\.ssh\\id_ed25519"
    assert meta.rows[files.fp].source == "ssh_dir"
    assert meta.upserts == 1
    provider.list_keys()
    assert meta.upserts == 1
    meta.set_label(files.fp, "Work laptop key")
    meta.set_hidden(files.fp, True)
    [key] = provider.list_keys()
    assert (key.label, key.hidden) == ("Work laptop key", True)
    files.private.rename(ssh_dir / "id_moved")
    assert files.public is not None
    files.public.rename(ssh_dir / "id_moved.pub")
    provider.list_keys()
    assert meta.rows[files.fp].last_path.endswith("\\id_moved")
    assert meta.upserts == 2


def test_list_is_sorted_by_name(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    for name in ("zeta", "Alpha", "id_rsa"):
        kit.write_key(ssh_dir, name)
    assert [k.name for k in provider.list_keys()] == ["Alpha", "id_rsa", "zeta"]


def test_default_key_preference(
    provider: FsKeyProvider, ssh_dir: Path, keys_dir: Path, settings: Any, meta: Any, kit: Any
) -> None:
    assert provider.default_key() is None
    kit.write_key(ssh_dir, "aaa")
    assert provider.default_key().name == "aaa"
    rsa = kit.write_key(ssh_dir, "id_rsa", "ssh-rsa", key_size=2048)
    assert provider.default_key().name == "id_rsa"
    kit.write_key(ssh_dir, "id_ecdsa", "ecdsa-sha2-nistp256")
    assert provider.default_key().name == "id_ecdsa"
    ed = kit.write_key(ssh_dir, "id_ed25519")
    kit.write_key(keys_dir, "id_ed25519")  # a generated key of the same name ranks after
    assert provider.default_key().fp == ed.fp
    settings.update({"default_key_fp": rsa.fp})
    assert provider.default_key().fp == rsa.fp
    meta.set_hidden(rsa.fp, True)
    assert provider.default_key().fp == ed.fp
    settings.update({"default_key_fp": "SHA256:" + "A" * 43})  # gone: fall back
    assert provider.default_key().fp == ed.fp


def test_get_and_resolve(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    with pytest.raises(NotFound) as exc:
        provider.resolve(None)
    assert exc.value.code == "key_unknown"
    files = kit.write_key(ssh_dir, "id_ed25519")
    assert provider.resolve(None).fp == files.fp
    assert provider.resolve(files.fp).name == "id_ed25519"
    with pytest.raises(NotFound) as exc:
        provider.get("SHA256:" + "B" * 43)
    assert exc.value.code == "key_unknown"


def test_load_plain_key(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    files = kit.write_key(ssh_dir, "id_ed25519", encoding="utf16")
    key = provider.load_private(files.fp)
    assert key.get_fingerprint("sha256") == files.fp
    assert key.get_comment() == "john@build-box"


@pytest.mark.parametrize(
    ("alg", "fmt", "kwargs"),
    [
        ("ssh-ed25519", "openssh", {}),
        ("ssh-rsa", "pkcs1-pem", {"key_size": 2048}),
        ("ecdsa-sha2-nistp256", "pkcs8-pem", {}),
    ],
)
def test_load_encrypted_key(
    provider: FsKeyProvider,
    ssh_dir: Path,
    kit: Any,
    caplog: pytest.LogCaptureFixture,
    alg: str,
    fmt: str,
    kwargs: dict[str, Any],
) -> None:
    caplog.set_level(logging.DEBUG)
    files = kit.write_key(ssh_dir, "id_enc", alg, fmt, kit.passphrase, encoding="crlf", **kwargs)
    with pytest.raises(KeyLocked):
        provider.load_private(files.fp)
    with pytest.raises(BadRequest) as exc:
        provider.load_private(files.fp, "wrong passphrase 123")
    assert exc.value.code == "bad_passphrase"
    assert exc.value.status == 400
    assert "wrong passphrase 123" not in str(exc.value)
    key = provider.load_private(files.fp, kit.passphrase)
    assert key.get_fingerprint("sha256") == files.fp
    assert kit.passphrase not in caplog.text
    assert "wrong passphrase 123" not in caplog.text
    assert "PRIVATE KEY" not in caplog.text


def test_load_prefers_vault(provider: FsKeyProvider, ssh_dir: Path, vault: Any, kit: Any) -> None:
    files = kit.write_key(ssh_dir, "id_enc", passphrase=kit.passphrase)
    vault.put(files.fp, files.key, 60)
    assert provider.load_private(files.fp) is files.key


def test_load_gone_or_mismatched(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    files = kit.write_key(ssh_dir, "id_rsa", "ssh-rsa", "pkcs1-pem", kit.passphrase, key_size=2048)
    record = provider.get(files.fp)
    files.private.unlink()
    with pytest.raises(KeyUnreadable):
        provider.load_private(files.fp, kit.passphrase)
    with pytest.raises(KeyUnreadable):
        load_private_from_disk(record, kit.passphrase)
    # An encrypted PEM key whose .pub belongs to another key: caught once decrypted.
    other = kit.write_key(ssh_dir, "other", "ssh-rsa", "pkcs1-pem", key_size=2048, pub=False)
    kit.write_key(
        ssh_dir, "id_rsa", "ssh-rsa", "pkcs1-pem", kit.passphrase, key=other.key, pub=False
    )
    assert files.public is not None
    assert files.public.exists()
    with pytest.raises(KeyUnreadable):
        provider.load_private(files.fp, kit.passphrase)


def test_name_available(provider: FsKeyProvider, keys_dir: Path, kit: Any) -> None:
    assert provider.name_available("id_ed25519_portkey") == (True, None)
    kit.write_key(keys_dir, "id_ed25519_portkey")
    assert provider.name_available("id_ed25519_portkey") == (False, "taken")
    assert provider.name_available("ID_ED25519_PORTKEY") == (False, "taken")
    assert provider.name_available("../id_rsa") == (False, "invalid")
    assert provider.name_available("x.pub") == (False, "invalid")
    assert provider.name_available("known_hosts") == (False, "invalid")
