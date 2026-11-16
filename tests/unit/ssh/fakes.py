"""Fakes for package 4's unit tests (no network; keys are generated per run).

- ``host_key(name, alg)``: a generated host key's public line ``"<type> <base64>"``, the same
  key for the same name within a test session.
- ``StubKnownHosts``: a ``KnownHostsReader`` answering one fixed ``KnownHostsMatch``.
- ``KwPrompter``: a ``FakePrompter`` that also takes the optional keywords package 4 added
  (``old_key_type``, ``retry``) and records them in ``extras``.
- ``FakeKeys``: a ``KeyProvider`` over in-memory keys, with an optional passphrase per key.
- ``hop(...)``/``target(...)``: ``HopRef``/``HopTarget`` builders.
- ``FakeWs``: the parts of a Starlette ``WebSocket`` that ``api.ws_common`` uses.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import asyncssh
from starlette.websockets import WebSocketState

from portkey.errors import BadRequest, ErrorCode, NotFound
from portkey.keys.vault import MemoryKeyVault
from portkey.models import HopRef, HopRole, HopTarget, KeyRecord, KnownHostsMatch
from portkey.ssh.errors import KeyLocked
from tests.fixtures.prompter import FakePrompter


@cache
def _generated(name: str, alg: str) -> asyncssh.SSHKey:
    return asyncssh.generate_private_key(alg)


def host_key(name: str, alg: str = "ssh-ed25519") -> str:
    key = _generated(name, alg)
    return " ".join(bytes(key.export_public_key("openssh")).decode().split()[:2])


def hop(
    index: int = 0,
    count: int = 1,
    *,
    name: str = "web-prod",
    host: str = "web-prod.example.test",
    port: int = 22,
    role: HopRole | None = None,
    connection_id: str | None = "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a",
    via: str | None = None,
) -> HopRef:
    role = role or ("target" if index == count - 1 else "jump")
    return HopRef(index, count, connection_id, name, host, port, role, via)


def target(ref: HopRef | None = None, **kw: Any) -> HopTarget:
    values: dict[str, Any] = {
        "ref": ref or hop(),
        "username": "john",
        "auth_method": "key",
        "key_fp": "SHA256:" + "A" * 43,
    }
    values.update(kw)
    return HopTarget(**values)


class FakeWs:
    """The parts of ``starlette.websockets.WebSocket`` that ws_common uses."""

    def __init__(self, *incoming: dict[str, Any]) -> None:
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for message in incoming:
            self.inbox.put_nowait(message)
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None
        self.application_state = WebSocketState.CONNECTING

    async def accept(self) -> None:
        self.application_state = WebSocketState.CONNECTED

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send_json(self, data: Any) -> None:
        self.sent.append(json.loads(json.dumps(data)))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = code
        self.application_state = WebSocketState.DISCONNECTED


@dataclass
class StubKnownHosts:
    match: KnownHostsMatch = field(default_factory=KnownHostsMatch)
    asked: list[tuple[str, int]] = field(default_factory=list)

    def lookup(self, host: str, port: int) -> KnownHostsMatch:
        self.asked.append((host, port))
        return self.match


class KwPrompter(FakePrompter):
    """Takes ``old_key_type`` and ``retry`` like ``WsPrompter``; records them in ``extras``."""

    def __init__(self, **scripts: Any) -> None:
        super().__init__(**scripts)
        self.extras: list[dict[str, Any]] = []

    async def hostkey_changed(
        self,
        hop: HopRef,
        key_type: str,
        old_fingerprint: str,
        new_fingerprint: str,
        first_seen_at: datetime | None,
        *,
        old_key_type: str | None = None,
    ) -> tuple[bool, str]:
        self.extras.append({"old_key_type": old_key_type})
        return await super().hostkey_changed(
            hop, key_type, old_fingerprint, new_fingerprint, first_seen_at
        )

    async def kbdint(
        self,
        hop: HopRef,
        name: str,
        instructions: str,
        prompts: list[tuple[str, bool]],
        *,
        retry: bool = False,
    ) -> list[str]:
        self.extras.append({"retry": retry})
        return await super().kbdint(hop, name, instructions, prompts)


class FakeKeys:
    """``KeyProvider`` over generated keys. ``add(name, passphrase=None)`` returns the record."""

    def __init__(self) -> None:
        self.records: dict[str, KeyRecord] = {}
        self.private: dict[str, asyncssh.SSHKey] = {}
        self.passphrases: dict[str, str] = {}
        self.vault = MemoryKeyVault()
        self.loads: list[str] = []

    def add(self, name: str, passphrase: str | None = None) -> KeyRecord:
        key = asyncssh.generate_private_key("ssh-ed25519")
        fp = str(key.get_fingerprint("sha256"))
        record = KeyRecord(
            fp=fp,
            name=name,
            type="ed25519",
            bits=256,
            comment="",
            path=Path("/ssh") / name,
            display_path=f"C:\\Users\\john\\.ssh\\{name}",
            source="ssh_dir",
            encrypted=passphrase is not None,
            public_key=bytes(key.export_public_key("openssh")).decode().strip(),
        )
        self.records[fp] = record
        self.private[fp] = key
        if passphrase is not None:
            self.passphrases[fp] = passphrase
        return record

    def get(self, fp: str) -> KeyRecord:
        if fp not in self.records:
            raise NotFound("unknown", code=ErrorCode.KEY_UNKNOWN)
        return self.records[fp]

    def resolve(self, key_fp: str | None) -> KeyRecord:
        if key_fp is None:
            if not self.records:
                raise NotFound("none", code=ErrorCode.KEY_UNKNOWN)
            return next(iter(self.records.values()))
        return self.get(key_fp)

    def load_private(self, fp: str, passphrase: str | None = None) -> asyncssh.SSHKey:
        self.loads.append(fp)
        held = self.vault.get(fp)
        if held is not None:
            return held
        record = self.get(fp)
        if record.encrypted:
            if not passphrase:
                raise KeyLocked(f"{record.name} is protected by a passphrase.")
            if passphrase != self.passphrases[fp]:
                raise BadRequest("wrong", code=ErrorCode.BAD_PASSPHRASE)
        return self.private[fp]
