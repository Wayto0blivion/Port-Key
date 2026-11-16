"""Hosts: /api/hosts/suggestions*, /api/hostkeys*."""

from __future__ import annotations

from datetime import datetime

from portkey.api.schemas.common import Hostname, Port, Request, Response


class SuggestionOut(Response):
    host: str  # preferred name (a host name before an IP address)
    port: int
    aliases: list[str]  # other names/addresses that share the key
    key_types: list[str]  # e.g. ["ssh-ed25519"]


class SuggestionList(Response):
    # Left out: dismissed hosts, and hosts a saved connection already uses (by host or alias,
    # at the same port).
    suggestions: list[SuggestionOut]
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
    # The route: the jump connection this host is reached through (null: directly).
    # ``via_name`` is null when that connection has been deleted since.
    via_id: str | None
    via_name: str | None
    # A known_hosts key the owner replaced in Port-Key: no longer trusted on this route.
    # Forgetting this record makes known_hosts trust it again.
    distrusted: bool


class HostKeyList(Response):
    host_keys: list[HostKeyOut]


class ForgetHostKeyRequest(Request):
    confirm_host: Hostname  # must equal the record's host (case-insensitive)
