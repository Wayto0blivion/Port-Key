"""Key generation: round trips, create-only writes, name validation and traversal attempts."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from portkey.errors import Conflict, Invalid
from portkey.keys import keygen
from portkey.keys.discovery import FsKeyProvider
from portkey.keys.keygen import generate_key_files, validate_key_name
from portkey.models import KeyGenSpec

COMMENT = "john@WORKPC port-key 2026-11-14"


def _spec(name: str = "id_ed25519_portkey", **kw: Any) -> KeyGenSpec:
    values: dict[str, Any] = {"type": "ed25519", "bits": None, "comment": COMMENT}
    values.update(kw)
    return KeyGenSpec(name=name, **values)


def _listing(path: Path) -> list[str]:
    return sorted(os.listdir(path))


def test_ed25519_round_trip_without_passphrase(keys_dir: Path) -> None:
    path = generate_key_files(_spec(), keys_dir)
    assert path == keys_dir / "id_ed25519_portkey"
    assert _listing(keys_dir) == ["id_ed25519_portkey", "id_ed25519_portkey.pub"]
    raw = path.read_bytes()
    assert raw.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----\n")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    key = asyncssh.read_private_key(str(path))
    pub_text = (keys_dir / "id_ed25519_portkey.pub").read_text()
    assert pub_text.endswith(f" {COMMENT}\n")
    assert "\r" not in pub_text
    assert asyncssh.import_public_key(pub_text).get_fingerprint() == key.get_fingerprint()
    assert key.get_comment() == COMMENT
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((keys_dir / "id_ed25519_portkey.pub").stat().st_mode) == 0o644


def test_round_trip_with_passphrase(keys_dir: Path, kit: Any) -> None:
    path = generate_key_files(_spec(passphrase=kit.passphrase), keys_dir)
    assert b"aes256-ctr" in base64.b64decode("".join(path.read_text().splitlines()[1:-1]))
    with pytest.raises(asyncssh.KeyImportError):
        asyncssh.read_private_key(str(path))
    with pytest.raises(asyncssh.KeyEncryptionError):
        asyncssh.read_private_key(str(path), "not it")
    key = asyncssh.read_private_key(str(path), kit.passphrase)
    assert key.get_algorithm() == "ssh-ed25519"


@pytest.mark.parametrize(
    ("key_type", "bits", "alg", "expected_bits"),
    [
        ("rsa", None, "ssh-rsa", 4096),
        ("rsa", 2048, "ssh-rsa", 2048),
        ("ecdsa", None, "ecdsa-sha2-nistp256", 256),
        ("ecdsa", 521, "ecdsa-sha2-nistp521", 521),
    ],
)
def test_other_types(
    provider: FsKeyProvider,
    key_type: str,
    bits: int | None,
    alg: str,
    expected_bits: int,
) -> None:
    record = provider.generate(_spec(f"id_{key_type}_{bits}", type=key_type, bits=bits))
    assert (record.type, record.bits, record.source, record.encrypted) == (
        key_type,
        expected_bits,
        "generated",
        False,
    )
    assert asyncssh.read_private_key(str(record.path)).get_algorithm() == alg


def test_provider_generate_lists_encrypted_key(
    provider: FsKeyProvider, meta: Any, kit: Any
) -> None:
    record = provider.generate(_spec(passphrase=kit.passphrase))
    assert record.encrypted is True
    assert record.display_path == "C:\\Users\\john\\.ssh\\port-key\\id_ed25519_portkey"
    assert record.comment == COMMENT
    assert meta.rows[record.fp].source == "generated"
    [listed] = provider.list_keys()
    assert listed == record
    key = provider.load_private(record.fp, kit.passphrase)
    assert key.get_fingerprint("sha256") == record.fp


@pytest.mark.parametrize(
    "name",
    [
        "../evil",
        "..",
        ".",
        "a/b",
        "/etc/passwd",
        "a\\b",
        "..\\..\\evil",
        ".hidden",
        "-flag",
        "id\x00x",
        "id x",
        "",
        "a" * 65,
        "key.pub",
        "key.PUB",
        "key.ppk",
        "known_hosts",
        "authorized_keys",
        "config",
        "c:evil",
        "ключ",
    ],
)
def test_invalid_names_write_nothing(keys_dir: Path, tmp_path: Path, name: str) -> None:
    before = _listing(tmp_path)
    assert not validate_key_name(name)
    with pytest.raises(Invalid) as exc:
        generate_key_files(_spec(name), keys_dir)
    assert exc.value.code == "key_name_invalid"
    assert _listing(keys_dir) == []
    assert _listing(tmp_path) == before


@pytest.mark.parametrize("existing", ["id_x", "id_x.pub", "ID_X", "Id_X.pub"])
def test_never_overwrites(keys_dir: Path, existing: str) -> None:
    (keys_dir / existing).write_text("owner's file\n")
    with pytest.raises(Conflict) as exc:
        generate_key_files(_spec("id_x"), keys_dir)
    assert exc.value.code == "key_name_taken"
    assert exc.value.message == "A key named id_x already exists. Choose another name."
    assert _listing(keys_dir) == [existing]
    assert (keys_dir / existing).read_text() == "owner's file\n"


def test_race_on_final_name_is_a_conflict(keys_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The name check passes, then another writer creates the .pub: O_EXCL still refuses."""
    monkeypatch.setattr(keygen, "name_taken", lambda name, d: False)
    (keys_dir / "id_x.pub").write_text("someone else's\n")
    with pytest.raises(Conflict):
        generate_key_files(_spec("id_x"), keys_dir)
    assert _listing(keys_dir) == ["id_x.pub"]
    assert (keys_dir / "id_x.pub").read_text() == "someone else's\n"


def test_symlink_at_target_is_not_followed(
    keys_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keygen, "name_taken", lambda name, d: False)
    victim = tmp_path / "victim"
    os.symlink(victim, keys_dir / "id_x")  # dangling link to a path outside
    with pytest.raises(Conflict):
        generate_key_files(_spec("id_x"), keys_dir)
    assert not victim.exists()
    assert _listing(keys_dir) == ["id_x"]


def test_write_failure_leaves_nothing_behind(
    keys_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(keygen.os, "replace", boom)
    with pytest.raises(Exception, match="couldn't write"):
        generate_key_files(_spec("id_x"), keys_dir)
    assert _listing(keys_dir) == []


def test_bad_bits_rejected(keys_dir: Path) -> None:
    with pytest.raises(Invalid):
        generate_key_files(_spec("id_x", type="rsa", bits=1024), keys_dir)
    assert _listing(keys_dir) == []
