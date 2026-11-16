"""Hosts: /api/hosts/suggestions*, /api/hostkeys*."""

from __future__ import annotations

from datetime import datetime

from portkey.api.schemas.common import Hostname, Port, Request, Response


class SuggestionOut(Response):
    host: str  # preferred name (a host name before an IP address)
    port: int
    aliases: list[str]  # other names/addresses that share the key
    key_types: list[str]  # e.g. ["ssh-ed25519"]
    saved: bool  # a saved connection already uses host (or an alias) and port


class SuggestionList(Response):
    suggestions: list[SuggestionOut]  # dismissed ones are left out
    hashed_count: int


class HostPort(Request):
    host: Hostname
    port: Port = 22


class HostKeyOut(Response):
    id: int
    host: str
    port: int
    key_type: str
    fp_sha256: str
    public_key: str
    first_seen_at: datetime
    last_seen_at: datetime


class HostKeyList(Response):
    host_keys: list[HostKeyOut]


class ForgetHostKeyRequest(Request):
    confirm_host: Hostname  # must equal the record's host (case-insensitive)
