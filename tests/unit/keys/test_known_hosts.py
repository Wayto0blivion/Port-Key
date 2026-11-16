"""known_hosts parsing, matching (plain, hashed, [host]:port, markers, wildcards) and
suggestions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from portkey.keys._kh_parse import hash_name, lookup_name, parse_known_hosts
from portkey.keys.known_hosts import FileKnownHosts, build_suggestions


def _key(alg: str = "ssh-ed25519") -> str:
    k = asyncssh.generate_private_key(alg)
    return bytes(k.export_public_key("openssh")).decode().strip()


@pytest.fixture(scope="module")
def keys() -> dict[str, str]:
    return {name: _key() for name in ("web", "build", "img", "vpn", "ca", "bad", "wild", "hashed")}


@pytest.fixture
def kh_path(tmp_path: Path) -> Path:
    return tmp_path / "known_hosts"


def _write(path: Path, lines: list[str], encoding: str = "lf", kit: Any = None) -> None:
    text = "\n".join(lines) + "\n"
    path.write_bytes(kit.encode(text, encoding) if kit else text.encode())


def test_lookup_name() -> None:
    assert lookup_name("Web-Prod", 22) == "web-prod"
    assert lookup_name("192.168.10.31", 2222) == "[192.168.10.31]:2222"


def test_plain_ports_case_and_comments(kh_path: Path, keys: dict[str, str], kit: Any) -> None:
    _write(
        kh_path,
        [
            "# a comment",
            "",
            f"web-prod,192.168.10.42 {keys['web']} john@WORKPC",
            f"[imaging-01]:2222,[192.168.10.31]:2222 {keys['img']}",
            "garbage line",
            "web-prod ssh-ed25519 not-base64!!",
            f"web-prod ssh-rsa {keys['web'].split()[1]}",  # type does not match the key
        ],
        "utf16",
        kit,
    )
    kh = FileKnownHosts(kh_path)
    web = kit.public_of(keys["web"])
    assert kh.lookup("web-prod", 22).trusted == (web,)
    assert kh.lookup("WEB-PROD", 22).trusted == (web,)
    assert kh.lookup("192.168.10.42", 22).trusted == (web,)
    assert kh.lookup("web-prod", 2222).trusted == ()
    assert kh.lookup("imaging-01", 2222).trusted == (kit.public_of(keys["img"]),)
    assert kh.lookup("imaging-01", 22).trusted == ()
    assert kh.is_trusted("web-prod", 22, keys["web"] + " with a comment")
    assert not kh.is_trusted("web-prod", 22, keys["img"])
    assert not kh.is_trusted("web-prod", 22, "not a key")


def test_hashed_entries(kh_path: Path, keys: dict[str, str], kit: Any) -> None:
    salt = bytes(range(20))
    _write(
        kh_path,
        [
            f"{hash_name('build-box', salt)} {keys['build']}",
            f"{hash_name('[192.168.10.31]:2222', salt[::-1])} {keys['img']}",
            f"|1|bm90LWJhc2U2NA|xx== {keys['bad']}",  # malformed hash: never matches
        ],
    )
    kh = FileKnownHosts(kh_path)
    assert kh.lookup("build-box", 22).trusted == (kit.public_of(keys["build"]),)
    assert kh.lookup("Build-Box", 22).trusted == (kit.public_of(keys["build"]),)
    assert kh.lookup("build-box", 2222).trusted == ()
    assert kh.is_trusted("192.168.10.31", 2222, keys["img"])
    assert not kh.is_trusted("192.168.10.31", 22, keys["img"])


def test_markers_and_wildcards(kh_path: Path, keys: dict[str, str], kit: Any) -> None:
    _write(
        kh_path,
        [
            f"*.lan,!secret.lan {keys['wild']}",
            f"web-prod {keys['web']}",
            f"web-pro? {keys['bad']}",
            f"@revoked web-prod {keys['bad']}",
            f"@cert-authority *.lan,[*.lan]:2222 {keys['ca']}",
            f"@bogus web-prod {keys['vpn']}",
            f"[vpn-*]:* {keys['vpn']}",
        ],
    )
    kh = FileKnownHosts(kh_path)
    web = kh.lookup("web-prod", 22)
    assert web.trusted == (kit.public_of(keys["web"]),)  # the revoked key is not trusted
    assert web.revoked == (kit.public_of(keys["bad"]),)
    assert kh.is_revoked("web-prod", 22, keys["bad"])
    assert kh.lookup("parser.lan", 22).trusted == (kit.public_of(keys["wild"]),)
    assert kh.lookup("parser.lan", 22).cert_authorities == (kit.public_of(keys["ca"]),)
    assert kh.lookup("parser.lan", 2222).cert_authorities == (kit.public_of(keys["ca"]),)
    assert kh.lookup("secret.lan", 22).trusted == ()  # negated
    assert kh.lookup("web-proxy", 22).trusted == ()  # ? is one character
    assert kh.lookup("vpn-gw", 2201).trusted == (kit.public_of(keys["vpn"]),)
    assert kh.lookup("vpn-gw", 22).trusted == ()


def test_rereads_when_file_changes(kh_path: Path, keys: dict[str, str]) -> None:
    kh = FileKnownHosts(kh_path)
    assert kh.lookup("web-prod", 22).trusted == ()  # missing file
    _write(kh_path, [f"web-prod {keys['web']}"])
    assert len(kh.lookup("web-prod", 22).trusted) == 1
    _write(kh_path, [f"build-box {keys['build']}", f"vpn-gw {keys['vpn']}"])
    os.utime(kh_path, ns=(1, 1))
    assert kh.lookup("web-prod", 22).trusted == ()
    assert len(kh.lookup("vpn-gw", 22).trusted) == 1
    kh_path.unlink()
    assert kh.suggestions().suggestions == ()


def test_unreadable_file_is_empty(kh_path: Path) -> None:
    kh_path.write_bytes(b"\xff\x00\xc3( not utf-8")
    kh = FileKnownHosts(kh_path)
    assert kh.lookup("web-prod", 22).trusted == ()


def test_suggestions_merge_and_skip(keys: dict[str, str]) -> None:
    rsa = _key("ssh-rsa")
    salt = b"s" * 20
    entries = parse_known_hosts(
        "\n".join(
            [
                f"web-prod {keys['web']}",
                f"192.168.10.42 {keys['web']}",  # same key: merged into web-prod
                f"web-prod,192.168.10.42 {rsa}",  # second key type of the same host
                f"192.168.10.20,build-box {keys['build']}",  # name preferred over IP
                f"[imaging-01]:2222,imaging-01 {keys['img']}",  # different ports: separate
                f"10.8.0.1 {keys['vpn']}",  # IP only
                f"*.lan {keys['wild']}",  # wildcard: not suggested
                f"{hash_name('parser-vm', salt)} {keys['hashed']}",
                f"{hash_name('backup-nas', salt)} {keys['hashed']}",
                f"@cert-authority *.lan {keys['ca']}",
                f"@revoked old-box {keys['bad']}",
            ]
        )
    )
    scan = build_suggestions(entries)
    assert scan.hashed_count == 2
    got = {(s.host, s.port): (s.aliases, s.key_types) for s in scan.suggestions}
    assert got == {
        ("10.8.0.1", 22): ((), ("ssh-ed25519",)),
        ("build-box", 22): (("192.168.10.20",), ("ssh-ed25519",)),
        ("imaging-01", 22): ((), ("ssh-ed25519",)),
        ("imaging-01", 2222): ((), ("ssh-ed25519",)),
        ("web-prod", 22): (("192.168.10.42",), ("ssh-ed25519", "ssh-rsa")),
    }
    assert [s.host for s in scan.suggestions] == [
        "10.8.0.1",
        "build-box",
        "imaging-01",
        "imaging-01",
        "web-prod",
    ]


def test_suggestions_from_file(kh_path: Path, keys: dict[str, str], kit: Any) -> None:
    _write(kh_path, [f"Parser-VM,192.168.10.27 {keys['web']}"], "bom", kit)
    [s] = FileKnownHosts(kh_path).suggestions().suggestions
    assert (s.host, s.port, s.aliases) == ("parser-vm", 22, ("192.168.10.27",))
