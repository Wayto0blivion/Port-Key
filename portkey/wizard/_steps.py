"""Turning the SSH core's progress into wizard ``step`` messages.

``Reporter`` sends ``step`` messages and remembers which steps are running, so a failure can
be shown on the step where it happened. ``StepPrompter`` sits between the SSH core and the
browser's prompter: every prompt is passed through untouched (replies are returned, never
kept), and ``status`` calls, which /ws/install does not send, become steps:

==========================================  ===============================================
SSH core                                    wizard step
==========================================  ===============================================
``connecting`` (any hop)                    ``connect`` running (``hop`` set); the previous
                                            hop's ``connect`` turns ok
``hostkey`` (target)                        ``connect`` ok, ``hostkey`` running
first ``auth`` on the target                ``hostkey`` ok with code known/trusted/accepted
``auth`` publickey (attempt "first")        ``try_key`` running
``auth`` password or keyboard-interactive   ``try_key`` warn ``refused`` (attempt "first"),
                                            ``password_login`` running
``ready``                                   ``try_key`` ok ``already_installed``, or
                                            ``password_login`` ok after a password
==========================================  ===============================================

Only the first connection of a run reports ``connect`` and ``hostkey``; later ones (verify,
check key) are quiet, and the flow reports their steps itself.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from portkey.api.schemas.ws import Hop, StepMsg, WizardStep
from portkey.interfaces import Prompter
from portkey.models import HopRef, KeyRecord

if TYPE_CHECKING:
    from portkey.wizard.install import WizardIO

StepState = Literal["running", "ok", "warn", "fail", "skipped"]
Attempt = Literal["first", "password", "key"]
HostKeyCode = Callable[[HopRef], Awaitable[str | None]]

SECRET_METHODS = ("password", "keyboard-interactive")


def hop_out(hop: HopRef) -> Hop:
    return Hop.model_validate(hop, from_attributes=True)


class Reporter:
    def __init__(self, io: WizardIO) -> None:
        self.io = io
        self.running: dict[WizardStep, HopRef | None] = {}

    async def send(
        self,
        step: WizardStep,
        state: StepState,
        *,
        code: str | None = None,
        detail: str | None = None,
        hop: HopRef | None = None,
    ) -> None:
        if state == "running":
            self.running[step] = hop
        else:
            self.running.pop(step, None)
        msg = StepMsg(
            step=step,
            state=state,
            code=code,
            detail=detail,
            hop=hop_out(hop) if hop is not None else None,
        )
        await self.io.step(msg)

    async def fail_running(self, code: str | None) -> None:
        """Mark every running step failed (the innermost first) with ``code``."""
        for step, hop in reversed(list(self.running.items())):
            await self.send(step, "fail", code=code, hop=hop)


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


class StepPrompter:
    """Implements ``interfaces.Prompter`` for the SSH core during a wizard run."""

    def __init__(self, inner: Prompter, reporter: Reporter, hostkey_code: HostKeyCode) -> None:
        self.inner = inner
        self.reporter = reporter
        self.hostkey_code = hostkey_code
        self.attempt: Attempt = "first"
        self.report_connect = True
        self.secret_started = False
        self._connecting: HopRef | None = None
        self._hostkey: HopRef | None = None
        self._accepted = False
        self._target_auth = False

    def begin(self, attempt: Attempt, *, report_connect: bool) -> None:
        """Start a new connection attempt."""
        self.attempt = attempt
        self.report_connect = report_connect
        self.secret_started = False
        self._connecting = None
        self._hostkey = None
        self._accepted = False
        self._target_auth = False

    # --- status -> steps ---------------------------------------------------------------------

    async def status(self, phase: str, hop: HopRef, **detail: Any) -> None:
        target = hop.role == "target"
        if phase == "connecting":
            await self._connect_ok()
            if self.report_connect:
                self._connecting = hop
                await self.reporter.send("connect", "running", hop=hop)
        elif phase == "hostkey" and target:
            await self._connect_ok()
            if self.report_connect:
                self._hostkey = hop
                await self.reporter.send("hostkey", "running", hop=hop)
        elif phase == "auth" and target:
            await self._target_auth_step(str(detail.get("method") or ""))
        elif phase == "ready" and target:
            await self.ensure_hostkey()
            await self._ready()

    async def _connect_ok(self) -> None:
        if self._connecting is not None:
            hop, self._connecting = self._connecting, None
            await self.reporter.send("connect", "ok", hop=hop)

    async def ensure_hostkey(self) -> None:
        """Report the target's host key check as done (once per attempt)."""
        if self._hostkey is None:
            return
        hop, self._hostkey = self._hostkey, None
        code = "accepted" if self._accepted else await self.hostkey_code(hop)
        await self.reporter.send("hostkey", "ok", code=code, hop=hop)

    async def _target_auth_step(self, method: str) -> None:
        await self.ensure_hostkey()
        if self.attempt == "key":
            return
        if method == "publickey" and self.attempt == "first":
            await self.reporter.send("try_key", "running")
        elif method in SECRET_METHODS and not self.secret_started:
            self.secret_started = True
            if self.attempt == "first":
                await self.reporter.send("try_key", "warn", code="refused")
            await self.reporter.send("password_login", "running")

    async def _ready(self) -> None:
        if self.attempt == "key":
            return
        if self.secret_started:
            await self.reporter.send("password_login", "ok")
        elif self.attempt == "first":
            await self.reporter.send("try_key", "ok", code="already_installed")

    # --- prompts: passed through -------------------------------------------------------------

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool:
        accepted = await self.inner.hostkey_new(hop, key_type, fingerprint)
        if accepted and hop.role == "target":
            self._accepted = True
        return accepted

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
        extra = (
            {"old_key_type": old_key_type}
            if _accepts(self.inner.hostkey_changed, "old_key_type")
            else {}
        )
        accept, name = await self.inner.hostkey_changed(
            hop, key_type, old_fingerprint, new_fingerprint, first_seen_at, **extra
        )
        if accept and hop.role == "target":
            self._accepted = True  # a wrong name still ends the run with hostkey_changed
        return accept, name

    async def passphrase(self, hop: HopRef, key: KeyRecord, retry: bool) -> tuple[str, bool]:
        return await self.inner.passphrase(hop, key, retry)

    async def password(self, hop: HopRef, user: str, retry: bool) -> str:
        return await self.inner.password(hop, user, retry)

    async def kbdint(
        self,
        hop: HopRef,
        name: str,
        instructions: str,
        prompts: list[tuple[str, bool]],
        *,
        retry: bool = False,
    ) -> list[str]:
        extra = {"retry": retry} if _accepts(self.inner.kbdint, "retry") else {}
        return await self.inner.kbdint(hop, name, instructions, prompts, **extra)
