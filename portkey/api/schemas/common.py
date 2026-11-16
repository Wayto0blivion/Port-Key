"""Shared field types, the error envelope and fingerprint id helpers."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from portkey.models import TAG_PATTERN


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _normalize_tag(value: object) -> object:
    """Same rule as ``interfaces.normalize_tag``: trim, drop a leading "#", lower-case."""
    return value.strip().lstrip("#").lower() if isinstance(value, str) else value


class Request(BaseModel):
    """Base for request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Response(BaseModel):
    """Base for response bodies. ``from_attributes`` lets routes validate domain records."""

    model_config = ConfigDict(from_attributes=True)


# --- field types --------------------------------------------------------------------------------

Color = Literal["red", "amber", "green", "blue", "purple", "grey"]
AuthMethod = Literal["key", "password"]
KeyType = Literal["ed25519", "rsa", "ecdsa"]
KeySource = Literal["ssh_dir", "generated"]

Uuid = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
Hostname = Annotated[
    str,
    BeforeValidator(_strip),
    StringConstraints(min_length=1, max_length=253, pattern=r"^[^\s@/]+$"),
]
Username = Annotated[
    str, BeforeValidator(_strip), StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")
]
Port = Annotated[int, Field(ge=1, le=65535)]
ConnName = Annotated[
    str,
    BeforeValidator(_strip),
    StringConstraints(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$"),
]
FolderName = ConnName
Notes = Annotated[str, StringConstraints(max_length=2000)]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")]
Tag = Annotated[str, BeforeValidator(_normalize_tag), StringConstraints(pattern=TAG_PATTERN)]
TagNames = Annotated[list[Tag], Field(max_length=32)]
Timestamp = str  # documented alias; responses use datetime and serialise as ISO-8601 "...Z"


# --- error envelope -----------------------------------------------------------------------------


class FieldError(Response):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(Response):
    code: str
    message: str
    fields: list[FieldError] | None = None
    dependants: list[ConnectionRefOut] | None = None
    retry_after: int | None = None


class ErrorResponse(Response):
    error: ErrorBody


class ConnectionRefOut(Response):
    id: str
    name: str


ErrorBody.model_rebuild()


# --- fingerprint ids in URLs ----------------------------------------------------------------------

_FP_ID = re.compile(r"^[A-Za-z0-9_-]{43}$")


def fp_to_id(fp: str) -> str:
    """``SHA256:ab+c/...`` -> ``ab-c_...``: URL-safe, used as ``{key_id}`` in paths."""
    return fp.removeprefix("SHA256:").replace("+", "-").replace("/", "_")


def fp_from_id(key_id: str) -> str | None:
    """Inverse of ``fp_to_id``; None if ``key_id`` is not a well-formed id."""
    if not _FP_ID.match(key_id):
        return None
    return "SHA256:" + key_id.replace("-", "+").replace("_", "/")
