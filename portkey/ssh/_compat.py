"""Calling a ``Prompter`` with the optional keywords added after the contract froze.

``Prompter.hostkey_changed(..., old_key_type=)`` and ``Prompter.kbdint(..., retry=)`` are
keyword-only extras with defaults (docs/handoff/wp4-ssh-core.md). Prompters written against the
first contract (the test bed's ``FakePrompter``) do not accept them, so the SSH core passes each
extra only to a callable whose signature takes it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


async def call_prompter(fn: Callable[..., Any], *args: Any, **optional: Any) -> Any:
    """``await fn(*args, **optional)``, leaving out keywords ``fn`` does not take."""
    kwargs = {k: v for k, v in optional.items() if _accepts(fn, k)}
    return await fn(*args, **kwargs)
