"""SSH wire-format helpers: public key blobs and the cleartext header of an OpenSSH private key.

No secrets pass through here: only public key blobs and the unencrypted header of
``openssh-key-v1`` files (cipher name, KDF name, public keys) are decoded.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass

from portkey.models import KeyType

OPENSSH_MAGIC = b"openssh-key-v1\x00"

# OpenSSH algorithm name -> (Port-Key key type, bits when fixed by the algorithm).
SUPPORTED_ALGORITHMS: dict[str, tuple[KeyType, int | None]] = {
    "ssh-ed25519": ("ed25519", 256),
    "ssh-rsa": ("rsa", None),
    "ecdsa-sha2-nistp256": ("ecdsa", 256),
    "ecdsa-sha2-nistp384": ("ecdsa", 384),
    "ecdsa-sha2-nistp521": ("ecdsa", 521),
}

_PEM_BODY = re.compile(
    r"-----BEGIN OPENSSH PRIVATE KEY-----\n(?P<body>.*?)-----END OPENSSH PRIVATE KEY-----",
    re.DOTALL,
)


class WireError(ValueError):
    """Malformed SSH wire data."""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise WireError("truncated")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def uint32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def string(self) -> bytes:
        return self.take(self.uint32())


@dataclass(frozen=True, slots=True)
class PublicBlob:
    algorithm: str
    blob: bytes

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.blob).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    @property
    def openssh(self) -> str:
        """``"<algorithm> <base64>"`` without a comment."""
        return f"{self.algorithm} {base64.b64encode(self.blob).decode('ascii')}"


def parse_public_blob(blob: bytes) -> PublicBlob:
    """Read the algorithm name from a public key blob. Raises ``WireError``."""
    try:
        algorithm = _Reader(blob).string().decode("ascii")
    except UnicodeDecodeError as exc:
        raise WireError("bad algorithm name") from exc
    if not algorithm:
        raise WireError("empty algorithm name")
    return PublicBlob(algorithm, blob)


def parse_public_line(line: str) -> tuple[PublicBlob, str]:
    """Parse ``"<type> <base64> [comment]"``. Returns (blob, comment). Raises ``WireError``
    when the base64 is bad or its algorithm differs from ``<type>``."""
    parts = line.strip().split(None, 2)
    if len(parts) < 2:
        raise WireError("not a public key line")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WireError("bad base64") from exc
    pub = parse_public_blob(blob)
    if pub.algorithm != parts[0]:
        raise WireError("key type does not match the key data")
    return pub, parts[2].strip() if len(parts) > 2 else ""


def key_bits(pub: PublicBlob) -> int:
    """Key size in bits for a supported algorithm. Raises ``WireError``."""
    kind = SUPPORTED_ALGORITHMS.get(pub.algorithm)
    if kind is None:
        raise WireError("unsupported algorithm")
    if kind[1] is not None:
        return kind[1]
    reader = _Reader(pub.blob)
    reader.string()  # algorithm
    reader.string()  # e
    modulus = reader.string().lstrip(b"\x00")
    if not modulus:
        raise WireError("empty RSA modulus")
    return int.from_bytes(modulus, "big").bit_length()


@dataclass(frozen=True, slots=True)
class OpenSshHeader:
    """The cleartext part of an ``openssh-key-v1`` private key file."""

    cipher: str
    kdf: str
    public: PublicBlob

    @property
    def encrypted(self) -> bool:
        return self.cipher != "none"


def parse_openssh_header(text: str) -> OpenSshHeader:
    """Read cipher, KDF and the first public key from an OpenSSH private key in PEM armour,
    without a passphrase. Raises ``WireError``."""
    match = _PEM_BODY.search(text)
    if match is None:
        raise WireError("no OpenSSH private key block")
    try:
        raw = base64.b64decode("".join(match.group("body").split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WireError("bad base64") from exc
    if not raw.startswith(OPENSSH_MAGIC):
        raise WireError("bad magic")
    reader = _Reader(raw[len(OPENSSH_MAGIC) :])
    try:
        cipher = reader.string().decode("ascii")
        kdf = reader.string().decode("ascii")
    except UnicodeDecodeError as exc:
        raise WireError("bad cipher name") from exc
    reader.string()  # kdf options
    if reader.uint32() < 1:
        raise WireError("no keys")
    return OpenSshHeader(cipher, kdf, parse_public_blob(reader.string()))
