"""Generate the TEST-ONLY fixtures under ``tests/fixtures/TEST-ONLY`` (package 7).

Run it in a container, never on the host: ``tests/fixtures/generate.sh`` (``--force`` replaces
every key). Key pairs are created only when missing, so a normal run leaves the committed keys
alone and only rewrites the derived files (known_hosts, authorized_keys, sshd seed files),
which are deterministic. Every key's comment and every path here says TEST-ONLY.

Writes:

- ``TEST-ONLY/sshd/hostkeys/{a,b}/ssh_host_ed25519_key[.pub]``: sshd host key sets A and B.
- ``TEST-ONLY/sshd/users.tsv``, ``keyonly_authorized_keys``, ``tfa_google_authenticator``,
  ``client_keys.pub``: what the sshd image is built from (see ``tests/sshd``).
- ``TEST-ONLY/ssh/``: the fixture ``.ssh`` directory: the client keys in ``testbed.CLIENT_KEYS``
  (plain and encrypted, ed25519, RSA and ECDSA, CRLF, UTF-8 BOM and UTF-16 variants), a CRLF
  ``known_hosts`` with plain, hashed and ``[host]:port`` entries plus markers and a wildcard,
  and files that must be reported as key problems.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import struct
import sys
from pathlib import Path

import asyncssh
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.fixtures import testbed as tb

HEADER = "TEST-ONLY fixture for Port-Key tests. Not a real host or key; never trust it elsewhere."


# --- encodings ----------------------------------------------------------------------------------


def encode(text: str, encoding: tb.Encoding) -> bytes:
    lf = text.replace("\r\n", "\n")
    if encoding == "lf":
        return lf.encode("utf-8")
    if encoding == "crlf":
        return lf.replace("\n", "\r\n").encode("utf-8")
    if encoding == "utf8-bom":
        return b"\xef\xbb\xbf" + lf.encode("utf-8")
    return b"\xff\xfe" + lf.replace("\n", "\r\n").encode("utf-16-le")


def write(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    path.chmod(mode)


# --- deterministic decoy host keys (public halves only, for known_hosts) ------------------------


def _ssh_string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def decoy_ed25519(label: str) -> str:
    seed = hashlib.sha256(f"TEST-ONLY decoy {label}".encode()).digest()
    pub = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key()
    raw = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    blob = _ssh_string(b"ssh-ed25519") + _ssh_string(raw)
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def decoy_ecdsa(label: str) -> str:
    scalar = int.from_bytes(hashlib.sha256(f"TEST-ONLY decoy {label}".encode()).digest(), "big")
    priv = ec.derive_private_key(scalar % (2**255), ec.SECP256R1())
    point = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    blob = _ssh_string(b"ecdsa-sha2-nistp256") + _ssh_string(b"nistp256") + _ssh_string(point)
    return "ecdsa-sha2-nistp256 " + base64.b64encode(blob).decode("ascii")


def hashed_host(host: str) -> str:
    """OpenSSH ``|1|salt|hash`` with a salt derived from the host, so output is stable."""
    salt = hashlib.sha1(f"TEST-ONLY salt {host}".encode()).digest()
    digest = hmac.new(salt, host.encode(), hashlib.sha1).digest()
    return f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"


# --- key pairs ----------------------------------------------------------------------------------


def make_hostkeys(force: bool) -> None:
    for key_set in ("a", "b"):
        path = tb.hostkey_path(key_set)  # type: ignore[arg-type]
        if path.exists() and not force:
            continue
        key = asyncssh.generate_private_key(
            "ssh-ed25519", comment=f"TEST-ONLY port-key sshd host key set {key_set.upper()}"
        )
        write(path, key.export_private_key("openssh"), 0o600)
        write(path.with_suffix(".pub"), key.export_public_key("openssh"))
        print(f"host key set {key_set}: {key.get_fingerprint()}")


def make_client_key(spec: tb.ClientKey, force: bool) -> str:
    """Create the key if missing (or forced); return its ``"<type> <base64>"``."""
    if spec.file.exists() and not force:
        raw = spec.file.read_bytes()
        text = raw.decode("utf-16") if raw.startswith(b"\xff\xfe") else raw.decode("utf-8-sig")
        key = asyncssh.import_private_key(text.replace("\r\n", "\n"), spec.passphrase)
    else:
        key = asyncssh.generate_private_key(
            spec.algorithm, comment=spec.comment, **({"key_size": spec.bits} if spec.bits else {})
        )
        key.set_comment(spec.comment)
        if spec.private_format == "pkcs1-pem":
            private = key.export_private_key(
                "pkcs1-pem", spec.passphrase, cipher_name="aes256-cbc"
            ).decode("ascii")
        else:
            private = key.export_private_key("openssh", spec.passphrase).decode("ascii")
        write(spec.file, encode(private, spec.private_encoding), 0o600)
        print(f"client key {spec.name}: {key.get_fingerprint()}")
    public = key.export_public_key("openssh").decode("ascii").split()
    pub_line = f"{public[0]} {public[1]} {spec.comment}\n"
    pub_path = spec.file.with_name(spec.file.name + ".pub")
    if spec.pub_file:
        write(pub_path, encode(pub_line, spec.pub_encoding))
    else:
        pub_path.unlink(missing_ok=True)
    return f"{public[0]} {public[1]}"


# --- derived files ------------------------------------------------------------------------------


def known_hosts() -> str:
    key_a = tb.hostkey_public("a")
    lines = [
        f"# {HEADER}",
        "# Written with CRLF line endings on purpose (a Windows-edited file).",
        "# sshd (host key set A): plain entry, then [host]:port for its second port.",
        f"sshd {key_a}",
        f"[sshd]:2222 {key_a}",
        "# sshd-inner (set A): hashed, so it matches when typed but is not suggested.",
        f"{hashed_host('sshd-inner')} {key_a}",
        "# sshd-rekeyed is deliberately absent (first-seen and changed-key tests set it up).",
        "# Fictional hosts for suggestions; decoy keys, nothing listens behind them.",
        f"web-prod,192.168.10.42 {decoy_ed25519('web-prod')}",
        f"web-prod,192.168.10.42 {decoy_ecdsa('web-prod')}",
        f"build-box,192.168.10.20 {decoy_ed25519('build-box')}",
        f"[imaging-01]:2222,[192.168.10.31]:2222 {decoy_ed25519('imaging-01')}",
        f"{hashed_host('vpn-gw')} {decoy_ed25519('vpn-gw')}",
        f"{hashed_host('10.8.0.1')} {decoy_ed25519('vpn-gw')}",
        "# A wildcard (skipped by suggestions), a revoked key and a CA.",
        f"*.lab.example {decoy_ed25519('wildcard')}",
        f"@revoked revoked-host {decoy_ed25519('revoked')}",
        f"@cert-authority *.corp.example {decoy_ed25519('ca')}",
        "",
    ]
    return "\n".join(lines)


def static_files() -> dict[str, str]:
    return {
        "work.ppk": (
            "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\n"
            "Comment: TEST-ONLY fake PuTTY file, not a real key\n"
            "Public-Lines: 1\nTEST-ONLY-NOT-A-KEY\nPrivate-Lines: 1\nTEST-ONLY-NOT-A-KEY\n"
            "Private-MAC: 0000\n"
        ),
        "broken_key": (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nTEST-ONLY this is not a key\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        ),
        "config": (
            f"# {HEADER}\nHost web-prod\n    HostName 192.168.10.42\n    User john\n"
            "Host imaging-01\n    HostName 192.168.10.31\n    Port 2222\n"
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="replace every key pair")
    args = parser.parse_args(argv)

    make_hostkeys(args.force)
    publics = {name: make_client_key(spec, args.force) for name, spec in tb.CLIENT_KEYS.items()}

    ssh = tb.SSH_FIXTURE_DIR
    write(ssh / "known_hosts", encode(known_hosts(), "crlf"))
    for name, text in static_files().items():
        write(ssh / name, text)
    seeded = [n for n in publics if n != "id_ed25519_portkey"]
    write(
        ssh / "authorized_keys", f"{publics['id_ed25519']} {tb.CLIENT_KEYS['id_ed25519'].comment}\n"
    )

    seed = tb.SSHD_SEED_DIR
    write(seed / "client_keys.pub", "".join(f"{publics[n]} {n}\n" for n in publics))
    write(
        seed / "keyonly_authorized_keys",
        "".join(f"{publics[n]} {tb.CLIENT_KEYS[n].comment}\n" for n in seeded),
    )
    write(
        seed / "users.tsv",
        f"# {HEADER}\n# user\tshell\tpassword\n"
        + "".join(f"{u.name}\t{u.shell}\t{u.password}\n" for u in tb.USERS.values()),
    )
    write(seed / "tfa_google_authenticator", f'{tb.TOTP_SECRET}\n" WINDOW_SIZE 3\n" TOTP_AUTH\n')
    write(tb.TEST_ONLY_DIR / "README.txt", f"{HEADER}\nSee tests/fixtures/README.md.\n")
    print(f"fixtures written under {tb.TEST_ONLY_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
