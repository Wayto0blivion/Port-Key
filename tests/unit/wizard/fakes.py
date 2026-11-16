"""A fake SSH layer and browser for the wizard's state-machine tests (no network).

- ``FakeConnector``: ``plan_new`` builds the hops like the real one; each ``connect`` call runs
  the next scripted attempt (an async function of ``(hops, prompter)``) and records the hops.
- Attempt builders: ``key_works``, ``password_works``, ``key_refused``, ``raises``. They drive
  the prompter as the SSH core does (status calls, prompts) and return a ``FakeChain``.
- ``FakeConn``: ``run`` answers from a handler ``(command, input) -> (stdout, stderr, status)``.
- ``FakeIO``: the ``WizardIO``; records steps and needs_action messages and answers actions
  from a list. ``Timeline`` collects steps, needs_action and chain closes in order.
- ``FakeKeys``: a ``KeyProvider`` with generated keys.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncssh

from portkey.api.schemas.ws import NeedsActionMsg, StepMsg, WizardAction
from portkey.errors import ErrorCode, NotFound
from portkey.interfaces import Prompter
from portkey.models import HopRef, HopTarget, KeyRecord, KnownHostsMatch
from portkey.ssh.errors import AuthFailed, SshError
from portkey.wizard.remote_script import MARK_ADDED, MARK_BEGIN

RunHandler = Callable[[str, str | None], tuple[str, str, int | None]]
Attempt = Callable[[list[HopTarget], Prompter], Awaitable["FakeChain"]]


class Timeline(list[str]):
    pass


def script_ok(command: str, stdin: str | None) -> tuple[str, str, int | None]:
    if command == "sh -s":
        if stdin and stdin.startswith("chmod"):
            return "", "", 0
        return f"{MARK_BEGIN}\n{MARK_ADDED}\n", "", 0
    return "portkey-ok\n", "", 0


class FakeConn:
    def __init__(self, handler: RunHandler = script_ok) -> None:
        self.handler = handler
        self.commands: list[tuple[str, str | None]] = []

    async def run(self, command: str, *, input: str | None = None, **_: Any) -> Any:
        self.commands.append((command, input))
        stdout, stderr, status = self.handler(command, input)
        return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=status)


class FakeChain:
    def __init__(self, conn: FakeConn, hops: list[HopTarget], timeline: Timeline) -> None:
        self.target = conn
        self.hops = [h.ref for h in hops]
        self.timeline = timeline
        self.closed = False

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.timeline.append(f"close {self.hops[-1].role}")


@dataclass
class FakeConnector:
    attempts: list[Attempt]
    timeline: Timeline = field(default_factory=Timeline)
    jumps: list[HopTarget] = field(default_factory=list)
    connects: list[list[HopTarget]] = field(default_factory=list)
    chains: list[FakeChain] = field(default_factory=list)

    def plan_new(self, jump_id: str | None, target: HopTarget) -> list[HopTarget]:
        jumps = self.jumps if jump_id else []
        count = len(jumps) + 1
        hops = [replace(j, ref=replace(j.ref, index=i, count=count)) for i, j in enumerate(jumps)]
        ref = replace(target.ref, index=count - 1, count=count, role="target")
        return [*hops, replace(target, ref=ref)]

    async def connect(self, hops: list[HopTarget], prompter: Prompter) -> FakeChain:
        self.connects.append(hops)
        assert self.attempts, "more connect() calls than scripted attempts"
        chain = await self.attempts.pop(0)(hops, prompter)
        chain.timeline = self.timeline
        self.chains.append(chain)
        return chain


async def _reach(
    hops: list[HopTarget], prompter: Prompter, *, new_key: bool = False, source: str | None = None
) -> None:
    for hop in hops:
        await prompter.status("connecting", hop.ref)
        extra = {"source": source} if source and hop.ref.role == "target" else {}
        await prompter.status("hostkey", hop.ref, **extra)
        if new_key and hop.ref.role == "target":
            assert await prompter.hostkey_new(hop.ref, "ssh-ed25519", "SHA256:" + "h" * 43)
        if hop.ref.role == "jump":
            await prompter.status("auth", hop.ref, method="publickey", key_name="id_ed25519")


def key_works(
    conn: FakeConn | None = None, *, new_key: bool = False, source: str | None = None
) -> Attempt:
    """The key is accepted. ``source`` is the SSH core's ``status hostkey`` source."""

    async def attempt(hops: list[HopTarget], prompter: Prompter) -> FakeChain:
        await _reach(hops, prompter, new_key=new_key, source=source)
        target = hops[-1]
        assert target.allow_key
        await prompter.status("auth", target.ref, method="publickey", key_name="id_ed25519")
        await prompter.status("ready", target.ref)
        return FakeChain(conn or FakeConn(), hops, Timeline())

    return attempt


def password_works(conn: FakeConn | None = None, *, kbdint: bool = False) -> Attempt:
    """The key (if allowed) is refused; the password (or a 2FA round) is accepted."""

    async def attempt(hops: list[HopTarget], prompter: Prompter) -> FakeChain:
        await _reach(hops, prompter)
        target = hops[-1]
        assert target.allow_password
        if target.allow_key:
            await prompter.status("auth", target.ref, method="publickey", key_name="id_ed25519")
        method = "keyboard-interactive" if kbdint else "password"
        await prompter.status("auth", target.ref, method=method)
        if kbdint:
            await prompter.kbdint(target.ref, "", "", [("Verification code: ", False)])
        else:
            await prompter.password(target.ref, target.username, False)
        await prompter.status("ready", target.ref)
        return FakeChain(conn or FakeConn(), hops, Timeline())

    return attempt


def key_refused(methods: tuple[str, ...] = ("publickey",), *, quiet: bool = False) -> Attempt:
    """The key is refused and nothing else is offered (``methods`` = what the server offers)."""

    async def attempt(hops: list[HopTarget], prompter: Prompter) -> FakeChain:
        if not quiet:
            await _reach(hops, prompter)
        target = hops[-1]
        if "publickey" in methods:
            await prompter.status("auth", target.ref, method="publickey", key_name="id_ed25519")
        raise AuthFailed("refused", methods=list(methods), hop=target.ref)

    return attempt


def raises(exc: SshError, *, reach: bool = True, on: str = "target") -> Attempt:
    async def attempt(hops: list[HopTarget], prompter: Prompter) -> FakeChain:
        if reach:
            await prompter.status("connecting", hops[0].ref)
        hop = hops[-1].ref if on == "target" else hops[0].ref
        exc.hop = hop
        raise exc

    return attempt


class FakeIO:
    def __init__(self, actions: list[WizardAction] | None = None, timeline: Timeline | None = None):
        self.actions = list(actions or [])
        self.timeline = timeline if timeline is not None else Timeline()
        self.steps: list[StepMsg] = []
        self.asked: list[NeedsActionMsg] = []

    async def step(self, msg: StepMsg) -> None:
        self.steps.append(msg)
        self.timeline.append(f"step {msg.step} {msg.state}")

    async def needs_action(self, msg: NeedsActionMsg) -> WizardAction:
        self.asked.append(msg)
        self.timeline.append(f"needs_action {msg.code}")
        assert self.actions, f"unscripted needs_action {msg.code}"
        return self.actions.pop(0)

    def trail(self) -> list[tuple[str, str, str | None]]:
        return [(s.step, s.state, s.code) for s in self.steps]


class FakeKeys:
    def __init__(self) -> None:
        self.records: dict[str, KeyRecord] = {}

    def add(self, name: str, *, public_key: str | None = None) -> KeyRecord:
        key = asyncssh.generate_private_key("ssh-ed25519")
        line = bytes(key.export_public_key("openssh")).decode().strip()
        record = KeyRecord(
            fp=str(key.get_fingerprint("sha256")),
            name=name,
            type="ed25519",
            bits=256,
            comment="TEST-ONLY",
            path=Path("/ssh") / name,
            display_path=f"C:\\Users\\john\\.ssh\\{name}",
            source="ssh_dir",
            encrypted=False,
            public_key=public_key if public_key is not None else f"{line} TEST-ONLY {name}",
        )
        self.records[record.fp] = record
        return record

    def resolve(self, key_fp: str | None) -> KeyRecord:
        if key_fp is None:
            if not self.records:
                raise NotFound("none", code=ErrorCode.KEY_UNKNOWN)
            return next(iter(self.records.values()))
        if key_fp not in self.records:
            raise NotFound("unknown", code=ErrorCode.KEY_UNKNOWN)
        return self.records[key_fp]


@dataclass
class StubKnownHosts:
    match: KnownHostsMatch = field(default_factory=KnownHostsMatch)

    def lookup(self, host: str, port: int) -> KnownHostsMatch:
        return self.match


def jump_target(name: str = "build-box") -> HopTarget:
    ref = HopRef(0, 1, "5a7d2e90-1c3b-4f6e-8d2a-9b0c1d2e3f4a", name, "build-box.test", 22, "jump")
    return HopTarget(ref, "john", "key", "SHA256:" + "J" * 43)
