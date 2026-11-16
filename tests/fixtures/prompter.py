"""A scripted ``portkey.interfaces.Prompter`` for tests (package 7).

Each prompt kind takes a script: one value (answered every time), a list (answered in order;
running out fails the test), a callable (called with the prompt's details), or ``CANCEL``
(raises ``PromptCancelled``, as a browser ``{"cancel": true}`` would). Every call is recorded::

    prompter = FakePrompter(
        hostkey_new=True,
        password=["wrong", "TEST-ONLY-pw-password"],  # first attempt fails, second works
        kbdint=kbdint_answers(USERS["tfa"].password, Totp()),
    )
    chain = await connector.connect(hops, prompter)
    assert prompter.kinds() == ["hostkey_new", "password", "password"]
    assert prompter.calls[1].detail["retry"] is False

Unscripted kinds fail the test with a clear message, so an unexpected prompt never hangs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from portkey.models import HopRef, KeyRecord
from portkey.ssh.errors import PromptCancelled
from tests.fixtures.testbed import Totp


class _Cancel:
    def __repr__(self) -> str:
        return "CANCEL"


#: Script value meaning "the user pressed Cancel".
CANCEL: Any = _Cancel()


@dataclass(frozen=True, slots=True)
class PromptCall:
    kind: str
    hop: HopRef
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StatusCall:
    phase: str
    hop: HopRef
    detail: dict[str, Any]


class UnexpectedPrompt(AssertionError):
    pass


@dataclass
class _Script:
    kind: str
    source: Any
    used: int = 0

    def answer(self, **info: Any) -> Any:
        source = self.source
        if source is None:
            raise UnexpectedPrompt(f"unscripted {self.kind} prompt: {info}")
        if isinstance(source, list):
            if self.used >= len(source):
                raise UnexpectedPrompt(
                    f"{self.kind} prompt #{self.used + 1} but only {len(source)} answers scripted"
                )
            value = source[self.used]
        elif callable(source) and not isinstance(source, _Cancel):
            value = source(**info)
        else:
            value = source
        self.used += 1
        if value is CANCEL:
            raise PromptCancelled()
        return value


class FakePrompter:
    """Implements ``Prompter``. Answers per kind (keyword arguments):

    - ``hostkey_new``: bool.
    - ``hostkey_changed``: ``(accept, confirm_name)``.
    - ``passphrase``: str (``remember`` comes from ``remember``) or ``(str, bool)``.
    - ``password``: str.
    - ``kbdint``: list of answers, one per prompt; see ``kbdint_answers``.
    """

    KINDS = ("hostkey_new", "hostkey_changed", "passphrase", "password", "kbdint")

    def __init__(self, *, remember: bool = False, **scripts: Any) -> None:
        unknown = set(scripts) - set(self.KINDS)
        if unknown:
            raise TypeError(f"unknown prompt kinds: {sorted(unknown)}")
        self.remember = remember
        self.calls: list[PromptCall] = []
        self.statuses: list[StatusCall] = []
        self._scripts = {kind: _Script(kind, scripts.get(kind)) for kind in self.KINDS}

    # --- inspection ------------------------------------------------------------------------

    def kinds(self) -> list[str]:
        return [c.kind for c in self.calls]

    def phases(self) -> list[tuple[int, str]]:
        """``(hop index, phase)`` for every status message, in order."""
        return [(s.hop.index, s.phase) for s in self.statuses]

    # --- Prompter --------------------------------------------------------------------------

    def _ask(self, kind: str, hop: HopRef, **detail: Any) -> Any:
        self.calls.append(PromptCall(kind, hop, detail))
        return self._scripts[kind].answer(hop=hop, **detail)

    async def status(self, phase: str, hop: HopRef, **detail: Any) -> None:
        self.statuses.append(StatusCall(phase, hop, dict(detail)))

    async def hostkey_new(self, hop: HopRef, key_type: str, fingerprint: str) -> bool:
        return bool(self._ask("hostkey_new", hop, key_type=key_type, fingerprint=fingerprint))

    async def hostkey_changed(
        self,
        hop: HopRef,
        key_type: str,
        old_fingerprint: str,
        new_fingerprint: str,
        first_seen_at: datetime | None,
    ) -> tuple[bool, str]:
        accept, name = self._ask(
            "hostkey_changed",
            hop,
            key_type=key_type,
            old_fingerprint=old_fingerprint,
            new_fingerprint=new_fingerprint,
            first_seen_at=first_seen_at,
        )
        return bool(accept), str(name)

    async def passphrase(self, hop: HopRef, key: KeyRecord, retry: bool) -> tuple[str, bool]:
        value = self._ask("passphrase", hop, key=key, retry=retry)
        if isinstance(value, tuple):
            return str(value[0]), bool(value[1])
        return str(value), self.remember

    async def password(self, hop: HopRef, user: str, retry: bool) -> str:
        return str(self._ask("password", hop, user=user, retry=retry))

    async def kbdint(
        self, hop: HopRef, name: str, instructions: str, prompts: list[tuple[str, bool]]
    ) -> list[str]:
        values = self._ask("kbdint", hop, name=name, instructions=instructions, prompts=prompts)
        return [str(v) for v in values]


def kbdint_answers(password: str, totp: Totp | None = None) -> Callable[..., list[str]]:
    """A ``kbdint`` script for PAM-style rounds: a "Password" prompt gets ``password``, a
    "Verification code" prompt gets a fresh TOTP code, anything else an empty string."""

    def answer(prompts: Sequence[tuple[str, bool]], **_: Any) -> list[str]:
        out = []
        for text, _echo in prompts:
            lowered = text.lower()
            if "verification code" in lowered or "one-time" in lowered:
                if totp is None:
                    raise UnexpectedPrompt(f"TOTP prompt {text!r} but no Totp given")
                out.append(totp.now())
            elif "password" in lowered:
                out.append(password)
            else:
                out.append("")
        return out

    return answer
