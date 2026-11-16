"""Keys: /api/keys/*. ``{key_id}`` in paths is ``fp_to_id(fp)`` (see common.py)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints, model_validator

from portkey.api.schemas.common import (
    AuthMethod,
    ConnectionRefOut,
    Fingerprint,
    KeySource,
    KeyType,
    Request,
    Response,
)

KeyFileName = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
]


class KeyInfo(Response):
    id: str  # URL-safe form of fp, use in /api/keys/{key_id}
    fp: str
    name: str  # file name, e.g. "id_ed25519"
    label: str | None
    type: KeyType
    bits: int
    comment: str
    path: str  # Windows path for display, never the container path
    source: KeySource
    encrypted: bool
    unlocked: bool  # held decrypted in memory right now
    public_key: str
    is_default: bool
    hidden: bool
    used_by: list[ConnectionRefOut]


class KeyProblemOut(Response):
    path: str  # Windows path
    reason: Literal["ppk", "not_private_key", "unreadable", "unsupported_type"]
    detail: str


class KeyListResponse(Response):
    keys: list[KeyInfo]  # hidden keys included with hidden=true
    problems: list[KeyProblemOut]
    default_fp: str | None
    unlocked_count: int


class GenerateKeyRequest(Request):
    name: KeyFileName
    type: KeyType = "ed25519"
    bits: int | None = None  # rsa: 2048|3072|4096 (default 4096); ecdsa: 256|384|521; ed25519: null
    comment: Annotated[str, StringConstraints(max_length=200, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""
    passphrase: Annotated[SecretStr, Field(max_length=1024)] | None = None

    @model_validator(mode="after")
    def _check_bits(self) -> GenerateKeyRequest:
        allowed: dict[str, set[int | None]] = {
            "ed25519": {None},
            "rsa": {None, 2048, 3072, 4096},
            "ecdsa": {None, 256, 384, 521},
        }
        if self.bits not in allowed[self.type]:
            raise ValueError(f"bits {self.bits} is not valid for {self.type}")
        if self.name.endswith(".pub") or self.name.endswith(".ppk"):
            raise ValueError("name must not end in .pub or .ppk")
        return self


class KeyNameCheck(Response):
    name: str
    available: bool
    reason: Literal["taken", "invalid"] | None = None


class KeyReassign(Request):
    """Where connections using a key go when it is hidden."""

    auth_method: AuthMethod
    key_fp: Fingerprint | None = None  # required when auth_method is "key"

    @model_validator(mode="after")
    def _check(self) -> KeyReassign:
        if self.auth_method == "key" and self.key_fp is None:
            raise ValueError("key_fp is required when auth_method is key")
        if self.auth_method == "password" and self.key_fp is not None:
            raise ValueError("key_fp must be null when auth_method is password")
        return self


class KeyUpdate(Request):
    label: Annotated[str, StringConstraints(max_length=64)] | None = None
    hidden: bool | None = None
    reassign: KeyReassign | None = None  # needed when hiding a key that connections use


class UnlockRequest(Request):
    passphrase: Annotated[SecretStr, Field(min_length=1, max_length=1024)]
    remember: bool = True  # true: keep for passphrase_ttl; false: keep for 60 s
