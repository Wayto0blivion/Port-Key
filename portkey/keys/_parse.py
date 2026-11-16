"""Recognise one private key file without its passphrase.

``parse_private_key`` takes the normalised text of a candidate file (and of its ``.pub``
companion, if any) and returns what the key list needs: public key, type, bits, comment and
whether the file is encrypted. Formats: OpenSSH (``openssh-key-v1``), PEM (PKCS#1 / SEC1) and
PKCS#8, encrypted or not. Encrypted OpenSSH keys are read from their cleartext public block;
encrypted PEM and PKCS#8 keys need the ``.pub`` file. Nothing decrypted leaves this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncssh

from portkey.keys._wire import (
    SUPPORTED_ALGORITHMS,
    PublicBlob,
    WireError,
    key_bits,
    parse_openssh_header,
    parse_public_blob,
    parse_public_line,
)
from portkey.models import KeyProblemReason, KeyType

_BEGIN = re.compile(r"-----BEGIN ([A-Z0-9 ]*?) ?PRIVATE KEY-----")
PPK_MAGIC = "PuTTY-User-Key-File-"


@dataclass(frozen=True, slots=True)
class ParsedKey:
    public: PublicBlob
    type: KeyType
    bits: int
    comment: str
    encrypted: bool

    @property
    def fp(self) -> str:
        return self.public.fingerprint

    @property
    def public_key(self) -> str:
        """``"<type> <base64> <comment>"`` (no trailing space when the comment is empty)."""
        return f"{self.public.openssh} {self.comment}".rstrip()


class ParseProblem(Exception):
    """The file is meant to be a key but cannot be used."""

    def __init__(self, reason: KeyProblemReason, detail: str) -> None:
        super().__init__(detail)
        self.reason: KeyProblemReason = reason
        self.detail = detail


def looks_like_ppk(text: str) -> bool:
    return text.lstrip().startswith(PPK_MAGIC)


def parse_companion(pub_text: str | None) -> tuple[PublicBlob, str] | None:
    """The first valid ``<type> <base64> [comment]`` line of a ``.pub`` file, or None."""
    if not pub_text:
        return None
    for line in pub_text.split("\n"):
        if line.strip() and not line.lstrip().startswith("#"):
            try:
                return parse_public_line(line)
            except WireError:
                return None
    return None


def parse_private_key(text: str, pub_text: str | None) -> ParsedKey | None:
    """Return the key, None when ``text`` is not a private key at all, or raise
    ``ParseProblem``."""
    if looks_like_ppk(text):
        raise ParseProblem("ppk", "PuTTY format, not supported")
    match = _BEGIN.search(text)
    if match is None:
        return None
    label = match.group(1).strip()
    companion = parse_companion(pub_text)

    if label == "OPENSSH":
        return _parse_openssh(text, companion)
    if label == "DSA":
        raise ParseProblem("unsupported_type", "DSA keys are not supported")
    if label == "ENCRYPTED" or "Proc-Type: 4,ENCRYPTED" in text:
        if companion is None:
            raise ParseProblem(
                "unreadable",
                "Encrypted key without a .pub file next to it. "
                "Port-Key needs the .pub file to list this key.",
            )
        public, comment = companion
        return _finish(public, comment, encrypted=True)
    try:
        key = asyncssh.import_private_key(text)
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise ParseProblem("unreadable", "Not a valid private key file") from exc
    public = parse_public_blob(bytes(key.public_data))
    comment = _companion_comment(companion, public) or (key.get_comment() or "")
    return _finish(public, comment, encrypted=False)


def _parse_openssh(text: str, companion: tuple[PublicBlob, str] | None) -> ParsedKey:
    try:
        header = parse_openssh_header(text)
    except WireError as exc:
        raise ParseProblem("unreadable", "Damaged OpenSSH key file") from exc
    public = header.public
    _check_supported(public)
    comment = _companion_comment(companion, public)
    if not header.encrypted:
        try:
            key = asyncssh.import_private_key(text)
        except (asyncssh.KeyImportError, ValueError) as exc:
            raise ParseProblem("unreadable", "Damaged OpenSSH key file") from exc
        if parse_public_blob(bytes(key.public_data)).blob != public.blob:
            raise ParseProblem("unreadable", "Damaged OpenSSH key file")
        comment = comment or (key.get_comment() or "")
    return _finish(public, comment, encrypted=header.encrypted)


def _companion_comment(companion: tuple[PublicBlob, str] | None, public: PublicBlob) -> str:
    if companion is None or companion[0].blob != public.blob:
        return ""
    return companion[1]


def _check_supported(public: PublicBlob) -> tuple[KeyType, int]:
    kind = SUPPORTED_ALGORITHMS.get(public.algorithm)
    if kind is None:
        raise ParseProblem("unsupported_type", f"{public.algorithm} keys are not supported")
    try:
        return kind[0], key_bits(public)
    except WireError as exc:
        raise ParseProblem("unreadable", "Damaged public key") from exc


def _finish(public: PublicBlob, comment: str, *, encrypted: bool) -> ParsedKey:
    key_type, bits = _check_supported(public)
    clean = " ".join(comment.split())
    return ParsedKey(public, key_type, bits, clean, encrypted)
