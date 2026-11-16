"""Key discovery: formats, encodings, encryption, problems, dedupe, display paths, fences."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from portkey.keys.discovery import FsKeyProvider

ALGS = {
    "ed25519": ("ssh-ed25519", {}, 256),
    "rsa": ("ssh-rsa", {"key_size": 2048}, 2048),
    "ecdsa": ("ecdsa-sha2-nistp256", {}, 256),
    "ecdsa384": ("ecdsa-sha2-nistp384", {}, 384),
}


def _only(provider: FsKeyProvider) -> Any:
    keys = provider.list_keys()
    assert len(keys) == 1, (keys, provider.problems())
    return keys[0]


@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
@pytest.mark.parametrize("with_pub", [True, False], ids=["pub", "nopub"])
@pytest.mark.parametrize("alg", list(ALGS))
def test_openssh_format(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any, alg: str, with_pub: bool, encrypted: bool
) -> None:
    name, kwargs, bits = ALGS[alg]
    files = kit.write_key(
        ssh_dir,
        "id_test",
        name,
        "openssh",
        kit.passphrase if encrypted else None,
        pub=with_pub,
        **kwargs,
    )
    key = _only(provider)
    assert key.fp == files.fp
    assert key.type == alg.removesuffix("384")
    assert key.bits == bits
    assert key.encrypted is encrypted
    assert kit.public_of(key.public_key) == kit.public_of(files.public_line)
    # The comment of an encrypted key without .pub is inside the encrypted part.
    assert key.comment == ("" if encrypted and not with_pub else "john@build-box")
    assert key.name == "id_test"
    assert key.display_path == "C:\\Users\\john\\.ssh\\id_test"
    assert key.source == "ssh_dir"
    assert key.path == ssh_dir / "id_test"
    assert provider.problems() == []


@pytest.mark.parametrize(
    ("alg", "fmt"),
    [
        ("ed25519", "pkcs8-pem"),
        ("rsa", "pkcs8-pem"),
        ("ecdsa", "pkcs8-pem"),
        ("rsa", "pkcs1-pem"),
        ("ecdsa384", "pkcs1-pem"),
    ],
)
@pytest.mark.parametrize("with_pub", [True, False], ids=["pub", "nopub"])
def test_pem_and_pkcs8_plain(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any, alg: str, fmt: str, with_pub: bool
) -> None:
    name, kwargs, bits = ALGS[alg]
    files = kit.write_key(ssh_dir, "id_pem", name, fmt, pub=with_pub, **kwargs)
    key = _only(provider)
    assert (key.fp, key.bits, key.encrypted) == (files.fp, bits, False)
    assert key.comment == ("john@build-box" if with_pub else "")


@pytest.mark.parametrize(("alg", "fmt"), [("rsa", "pkcs1-pem"), ("ecdsa", "pkcs8-pem")])
def test_pem_and_pkcs8_encrypted_need_pub(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any, alg: str, fmt: str
) -> None:
    name, kwargs, _ = ALGS[alg]
    files = kit.write_key(ssh_dir, "id_enc", name, fmt, kit.passphrase, **kwargs)
    key = _only(provider)
    assert key.encrypted is True
    assert key.fp == files.fp
    assert files.public is not None
    files.public.unlink()
    assert provider.list_keys() == []
    [problem] = provider.problems()
    assert problem.reason == "unreadable"
    assert problem.display_path.endswith("\\id_enc")
    assert ".pub" in problem.detail


@pytest.mark.parametrize("encoding", ["crlf", "bom", "utf16", "utf16be"])
@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
def test_windows_encodings(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any, encoding: str, encrypted: bool
) -> None:
    files = kit.write_key(
        ssh_dir, "id_ed25519", passphrase=kit.passphrase if encrypted else None, encoding=encoding
    )
    key = _only(provider)
    assert key.fp == files.fp
    assert key.comment == "john@build-box"
    assert key.encrypted is encrypted


def test_pub_comment_wins_and_mismatched_pub_is_ignored(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any
) -> None:
    files = kit.write_key(ssh_dir, "id_ed25519", comment="inner@build-box")
    assert files.public is not None
    files.public.write_text(files.public_line.replace("inner@", "outer@") + "\n")
    assert _only(provider).comment == "outer@build-box"
    other = asyncssh.generate_private_key("ssh-ed25519", comment="someone@else")
    files.public.write_bytes(bytes(other.export_public_key("openssh")))
    key = _only(provider)
    assert key.fp == files.fp
    assert key.comment == "inner@build-box"


def test_modes_0777_do_not_matter(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    kit.write_key(ssh_dir, "id_ed25519")
    kit.chmod_all(ssh_dir)
    assert _only(provider).name == "id_ed25519"


def test_problems_and_ignored_files(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    (ssh_dir / "work.ppk").write_text("PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\n")
    (ssh_dir / "putty_noext").write_text("PuTTY-User-Key-File-2: ssh-rsa\n")
    (ssh_dir / "broken").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nbm90IGEga2V5\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    (ssh_dir / "broken_pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nbm90IGEga2V5\n-----END PRIVATE KEY-----\n"
    )
    (ssh_dir / "old_dsa").write_text("-----BEGIN DSA PRIVATE KEY-----\nAAAA\n-----END DSA...\n")
    kit.write_key(ssh_dir, "id_dsa", "ssh-dss")
    kit.write_key(ssh_dir, "id_ed448", "ssh-ed448")
    orphan = asyncssh.generate_private_key("ssh-ed25519", comment="john@imaging-01")
    (ssh_dir / "imaging.pub").write_bytes(bytes(orphan.export_public_key("openssh")))
    # Not keys at all: ignored without a problem.
    (ssh_dir / "config").write_text("Host web-prod\n  HostName 192.168.10.42\n")
    (ssh_dir / "known_hosts").write_text("web-prod ssh-ed25519 AAAA\n")
    (ssh_dir / "notes.txt").write_text("remember the vpn-gw at 10.8.0.1\n")
    (ssh_dir / "binary.bin").write_bytes(bytes(range(256)))
    (ssh_dir / "garbage.pub").write_text("not a key\n")
    (ssh_dir / ".hidden_key").write_text("-----BEGIN PRIVATE KEY-----\n")
    (ssh_dir / "subdir").mkdir()
    kit.write_key(ssh_dir / "subdir", "id_nested")  # only .ssh's top level and port-key/

    assert provider.list_keys() == []
    reasons = {p.display_path.rsplit("\\", 1)[1]: p.reason for p in provider.problems()}
    assert reasons == {
        "work.ppk": "ppk",
        "putty_noext": "ppk",
        "broken": "unreadable",
        "broken_pem": "unreadable",
        "old_dsa": "unsupported_type",
        "id_dsa": "unsupported_type",
        "id_ed448": "unsupported_type",
        "imaging.pub": "not_private_key",
    }


def test_unreadable_file_is_a_problem(provider: FsKeyProvider, ssh_dir: Path, kit: Any) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    files = kit.write_key(ssh_dir, "id_locked")
    os.chmod(files.private, 0)
    [problem] = provider.problems()
    assert (problem.reason, problem.display_path) == (
        "unreadable",
        "C:\\Users\\john\\.ssh\\id_locked",
    )


def test_dedupe_prefers_generated_copy(
    provider: FsKeyProvider, ssh_dir: Path, keys_dir: Path, kit: Any
) -> None:
    files = kit.write_key(keys_dir, "id_portkey")
    kit.write_key(ssh_dir / "port-key", "id_portkey", key=files.key)
    kit.write_key(ssh_dir, "copy_of_portkey", key=files.key)
    (ssh_dir / "port-key" / "work.ppk").write_text("PuTTY-User-Key-File-3: x\n")
    (keys_dir / "work.ppk").write_text("PuTTY-User-Key-File-3: x\n")
    key = _only(provider)
    assert key.source == "generated"
    assert key.path == keys_dir / "id_portkey"
    assert key.display_path == "C:\\Users\\john\\.ssh\\port-key\\id_portkey"
    # The same Windows folder is mounted twice: its problems are reported once.
    assert [p.display_path for p in provider.problems()] == [
        "C:\\Users\\john\\.ssh\\port-key\\work.ppk"
    ]


def test_public_only_is_not_a_problem_when_private_is_elsewhere(
    provider: FsKeyProvider, ssh_dir: Path, keys_dir: Path, kit: Any
) -> None:
    files = kit.write_key(keys_dir, "id_portkey")
    assert files.public is not None
    (ssh_dir / "id_portkey.pub").write_bytes(files.public.read_bytes())
    assert len(provider.list_keys()) == 1
    assert provider.problems() == []


def test_symlinks_out_of_the_folders_are_not_followed(
    provider: FsKeyProvider, ssh_dir: Path, keys_dir: Path, tmp_path: Path, kit: Any
) -> None:
    outside = tmp_path / "outside"
    files = kit.write_key(outside, "secret_key")
    kit.write_key(outside / "dir", "inner_key")
    os.symlink(files.private, ssh_dir / "id_ed25519")
    os.symlink(outside / "dir", ssh_dir / "port-key")
    os.symlink(outside / "dir" / "inner_key", keys_dir / "linked")
    assert provider.list_keys() == []
    paths = sorted(p.display_path for p in provider.problems())
    assert paths == [
        "C:\\Users\\john\\.ssh\\id_ed25519",
        "C:\\Users\\john\\.ssh\\port-key\\linked",
    ]


def test_symlink_inside_the_folder_is_followed(
    provider: FsKeyProvider, ssh_dir: Path, kit: Any
) -> None:
    kit.write_key(ssh_dir / "port-key", "real")
    os.symlink(ssh_dir / "port-key" / "real", ssh_dir / "alias")
    keys = provider.list_keys()
    assert [k.name for k in keys] == ["real"]  # same fingerprint, generated copy wins


def test_missing_folders(config: Any, provider: FsKeyProvider) -> None:
    config.ssh_dir.rmdir()
    config.keys_dir.rmdir()
    assert provider.list_keys() == []
    assert provider.problems() == []
