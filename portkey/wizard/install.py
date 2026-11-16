"""The key install flow behind /ws/install (package 6).

Steps (docs/architecture.md section 8, docs/ws-protocol.md): validate -> connect (jump hops
first) -> hostkey -> try_key (key-only; success saves and finishes with outcome
``already_installed``) -> password_login (password and keyboard-interactive, 2FA relayed) ->
install (``remote_script``) -> verify (new key-only connection, ``echo portkey-ok``) ->
discard_password -> save (create, or update ``connection_id``; record ``key_installed``).
Recoverable failures send ``needs_action`` and wait for an ``action``. The password is held
only for the life of the run and dropped after use.
"""

from __future__ import annotations

from typing import Protocol

from portkey.api.schemas.ws import NeedsActionMsg, StartMsg, StepMsg, WizardAction
from portkey.interfaces import (
    ConnectionsRepo,
    EventLog,
    KeyProvider,
    Prompter,
    SshConnector,
)
from portkey.models import ConnectionRecord


class WizardIO(Protocol):
    """How the flow talks to the browser beyond prompts; implemented in ``api.ws_install``."""

    async def step(self, msg: StepMsg) -> None: ...

    async def needs_action(self, msg: NeedsActionMsg) -> WizardAction:
        """Send the message and wait for the user's ``action``."""
        ...


class InstallWizard:
    def __init__(
        self,
        connector: SshConnector,
        connections: ConnectionsRepo,
        keys: KeyProvider,
        events: EventLog,
    ) -> None:
        self.connector = connector
        self.connections = connections
        self.keys = keys
        self.events = events

    async def run(
        self, start: StartMsg, prompter: Prompter, io: WizardIO
    ) -> tuple[ConnectionRecord, str]:
        """Run the whole flow. Returns (saved connection, outcome) where outcome is
        "installed", "already_installed" or "saved_password_only". Raises ``SshError``
        subclasses, ``PromptCancelled`` (also for the ``cancel`` action) and
        ``PromptTimeout``."""
        raise NotImplementedError("package 6")
