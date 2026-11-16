"""The test bed in one place: sshd servers, users, passwords, TOTP, fixture keys (package 7).

Integration tests (``tests/integration``), the e2e harness (``tests/e2e``) and the fixture
generator (``generate.py``) all read these constants, so the sshd image, the fixture ``.ssh``
directory and the tests cannot drift apart. Everything here is TEST-ONLY: the passwords, the TOTP
secret and the key material under ``TEST-ONLY/`` exist only for the private compose networks.

After changing a constant that ends up in a file (passwords, TOTP secret, users), run
``tests/fixtures/generate.sh`` and rebuild the sshd image (``./mk integration`` does).
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FIXTURES_DIR = Path(__file__).resolve().parent
TEST_ONLY_DIR = FIXTURES_DIR / "TEST-ONLY"
#: The fixture ``.ssh`` directory (mounted into the ``app`` service, copied to 0777 there).
SSH_FIXTURE_DIR = TEST_ONLY_DIR / "ssh"
#: Seed files baked into the sshd image (host keys, users, authorized_keys, TOTP secret).
SSHD_SEED_DIR = TEST_ONLY_DIR / "sshd"
HOSTKEY_DIR = SSHD_SEED_DIR / "hostkeys"

# --- secrets that are not secret ------------------------------------------------------------

#: Base32 TOTP secret of the ``tfa`` user (pam_google_authenticator, 30 s steps, 6 digits).
TOTP_SECRET = "PORTKEYTESTONLYTOTPSECRETXYZABCD"
#: Passphrase of every encrypted fixture key (``id_ed25519_pass``, ``id_rsa_pass``).
KEY_PASSPHRASE = "TEST-ONLY fixture passphrase"
#: The app password the e2e harness sets on first run.
APP_PASSWORD = "TEST-ONLY app password"

# --- sshd users --------------------------------------------------------------------------------

AuthKind = Literal["password", "publickey", "keyboard-interactive"]


@dataclass(frozen=True, slots=True)
class SshUser:
    name: str
    password: str
    shell: str
    #: Methods the server accepts for this user, as sshd's AuthenticationMethods would say.
    methods: tuple[AuthKind, ...]
    note: str


def _pw(user: str) -> str:
    return f"TEST-ONLY-{user}-password"


USERS: dict[str, SshUser] = {
    u.name: u
    for u in (
        SshUser("pw", _pw("pw"), "/bin/bash", ("password",), "password only (Match User)"),
        SshUser(
            "keyonly",
            _pw("keyonly"),
            "/bin/bash",
            ("publickey",),
            "PasswordAuthentication no; fixture public keys pre-seeded",
        ),
        SshUser(
            "fresh",
            _pw("fresh"),
            "/bin/bash",
            ("publickey", "password", "keyboard-interactive"),
            "no ~/.ssh",
        ),
        SshUser(
            "tfa",
            _pw("tfa"),
            "/bin/bash",
            ("publickey", "keyboard-interactive"),
            "keyboard-interactive: 'Password: ' then 'Verification code: ' (TOTP_SECRET)",
        ),
        SshUser(
            "fishy",
            _pw("fishy"),
            "/usr/bin/fish",
            ("publickey", "password", "keyboard-interactive"),
            "login shell fish",
        ),
        SshUser(
            "badperms",
            _pw("badperms"),
            "/bin/bash",
            ("publickey", "password", "keyboard-interactive"),
            "~/.ssh is 0777, so StrictModes refuses its authorized_keys",
        ),
        SshUser(
            "nosh",
            _pw("nosh"),
            "/usr/sbin/nologin",
            ("publickey", "password", "keyboard-interactive"),
            "login shell nologin: sign-in works, every command fails",
        ),
    )
}

#: What nologin prints; a session as ``nosh`` exits 1 with this on stdout.
NOLOGIN_MESSAGE = "This account is currently not available."
#: The ``tfa`` user's keyboard-interactive prompts, in order (one round each).
TFA_PROMPTS = ("Password: ", "Verification code: ")
#: Users whose fixture authorized_keys holds every client key below except ``port-key/``.
PRESEEDED_KEY_USERS = ("keyonly",)

# --- sshd servers -------------------------------------------------------------------------------

HostKeySet = Literal["a", "b"]


@dataclass(frozen=True, slots=True)
class SshServer:
    """One sshd service as the test runner (or the app container) addresses it."""

    service: str
    host: str
    port: int
    hostkey_set: HostKeySet
    #: False when only reachable by jumping through ``sshd`` (it sits on the inner network).
    direct: bool = True


SSHD = SshServer("sshd", "sshd", 22, "a")
#: The same server on its second port, for ``[host]:port`` known_hosts entries.
SSHD_ALT_PORT = SshServer("sshd", "sshd", 2222, "a")
SSHD_REKEYED = SshServer("sshd-rekeyed", "sshd-rekeyed", 22, "b")
SSHD_INNER = SshServer("sshd-inner", "sshd-inner", 22, "a", direct=False)
SERVERS = (SSHD, SSHD_ALT_PORT, SSHD_REKEYED, SSHD_INNER)


def hostkey_path(hostkey_set: HostKeySet) -> Path:
    """Private host key of a set (TEST-ONLY). The ``.pub`` sits beside it."""
    return HOSTKEY_DIR / hostkey_set / "ssh_host_ed25519_key"


def hostkey_public(hostkey_set: HostKeySet) -> str:
    """``"ssh-ed25519 AAAA..."`` without the comment."""
    text = hostkey_path(hostkey_set).with_suffix(".pub").read_text(encoding="ascii")
    key_type, blob = text.split()[:2]
    return f"{key_type} {blob}"


def fingerprint(public_key: str) -> str:
    """OpenSSH SHA256 fingerprint of ``"<type> <base64> [comment]"``."""
    blob = base64.b64decode(public_key.split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return "SHA256:" + digest.rstrip("=")


def hostkey_fingerprint(hostkey_set: HostKeySet) -> str:
    return fingerprint(hostkey_public(hostkey_set))


# --- client keys in the fixture .ssh directory --------------------------------------------------

Encoding = Literal["lf", "crlf", "utf8-bom", "utf16le-bom-crlf"]


@dataclass(frozen=True, slots=True)
class ClientKey:
    """A TEST-ONLY private key in ``TEST-ONLY/ssh``. ``path`` is relative to that directory."""

    name: str
    path: str
    algorithm: str  # asyncssh / OpenSSH algorithm name
    encrypted: bool
    private_format: Literal["openssh", "pkcs1-pem"]
    private_encoding: Encoding
    pub_file: bool
    pub_encoding: Encoding = "lf"
    bits: int | None = None

    @property
    def file(self) -> Path:
        return SSH_FIXTURE_DIR / self.path

    @property
    def passphrase(self) -> str | None:
        return KEY_PASSPHRASE if self.encrypted else None

    @property
    def comment(self) -> str:
        return f"TEST-ONLY port-key fixture {self.name}"


CLIENT_KEYS: dict[str, ClientKey] = {
    k.name: k
    for k in (
        # The default key (preference order id_ed25519, id_ecdsa, id_rsa).
        ClientKey("id_ed25519", "id_ed25519", "ssh-ed25519", False, "openssh", "lf", True),
        # Windows-flavoured: private key with CRLF, .pub written by PowerShell 5 (UTF-16 LE).
        ClientKey(
            "id_rsa",
            "id_rsa",
            "ssh-rsa",
            False,
            "openssh",
            "crlf",
            True,
            "utf16le-bom-crlf",
            bits=2048,
        ),
        # No .pub: the public key comes from the private file's cleartext block. UTF-8 BOM.
        ClientKey(
            "id_ecdsa", "id_ecdsa", "ecdsa-sha2-nistp256", False, "openssh", "utf8-bom", False
        ),
        ClientKey("id_ed25519_pass", "id_ed25519_pass", "ssh-ed25519", True, "openssh", "lf", True),
        # Legacy encrypted PEM: no cleartext public block, so the .pub is needed.
        ClientKey(
            "id_rsa_pass", "id_rsa_pass", "ssh-rsa", True, "pkcs1-pem", "lf", True, bits=2048
        ),
        # As if generated in Port-Key and found again under .ssh\port-key.
        ClientKey(
            "id_ed25519_portkey",
            "port-key/id_ed25519_portkey",
            "ssh-ed25519",
            False,
            "openssh",
            "lf",
            True,
        ),
    )
}

#: Files in the fixture .ssh directory that must show up as key problems, with the reason.
PROBLEM_FILES: dict[str, str] = {
    "work.ppk": "ppk",
    "broken_key": "not_private_key",
}


def client_public(name: str) -> str:
    """``"<type> <base64>"`` of a fixture client key (read from the generated seed copy)."""
    for line in (SSHD_SEED_DIR / "client_keys.pub").read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == name:
            return f"{parts[0]} {parts[1]}"
    raise KeyError(name)


# --- TOTP ---------------------------------------------------------------------------------------


class Totp:
    """Codes for the ``tfa`` user. ``fresh()`` avoids handing out a code about to expire."""

    def __init__(self, secret: str = TOTP_SECRET) -> None:
        import pyotp  # dev dependency; imported lazily so the generator does not need it

        self._totp = pyotp.TOTP(secret)
        self.interval = int(self._totp.interval)

    def now(self) -> str:
        return str(self._totp.now())

    def remaining(self) -> float:
        return self.interval - (time.time() % self.interval)

    def wait_needed(self, min_remaining: float = 5.0) -> float:
        """Seconds to wait so that the next code stays valid for ``min_remaining`` seconds."""
        left = self.remaining()
        return left + 0.2 if left < min_remaining else 0.0

    def fresh(self, min_remaining: float = 5.0) -> str:
        """A code valid for at least ``min_remaining`` seconds (blocks; use ``afresh`` in async)."""
        time.sleep(self.wait_needed(min_remaining))
        return self.now()

    async def afresh(self, min_remaining: float = 5.0) -> str:
        import asyncio

        await asyncio.sleep(self.wait_needed(min_remaining))
        return self.now()
