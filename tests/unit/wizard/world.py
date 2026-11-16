"""A wizard wired to the fakes in ``fakes.py`` and a real SQLite store, plus run helpers.

- ``make_world(db, *attempts)``: an ``InstallWizard`` whose connector plays ``attempts`` in
  order; the ``world`` fixture (conftest) calls it with a fresh database.
- ``start(**fields)``: a valid ``StartMsg`` for web-prod (override any field).
- ``run(w, msg, actions)`` / ``run_fails(...)``: run the wizard with a ``FakePrompter`` that
  answers ``PASSWORD`` and accepts new host keys; ``actions`` answer the needs_action messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from portkey.api.schemas.ws import StartMsg, WizardAction
from portkey.config import Limits
from portkey.models import KeyRecord, KnownHostsMatch
from portkey.store.connections import SqliteConnectionsRepo
from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.groups import SqliteGroupsRepo
from portkey.store.hostkeys import SqliteHostKeyStore
from portkey.wizard.install import InstallWizard
from tests.fixtures.prompter import FakePrompter
from tests.unit.wizard.fakes import Attempt, FakeConnector, FakeIO, FakeKeys, StubKnownHosts

PASSWORD = "TEST-ONLY-wizard-unit-password-7f3a"


@dataclass
class World:
    wizard: InstallWizard
    connector: FakeConnector
    keys: FakeKeys
    key: KeyRecord
    db: Database
    known: StubKnownHosts

    @property
    def connections(self) -> SqliteConnectionsRepo:
        return self.wizard.connections  # type: ignore[return-value]

    def events(self) -> list[Any]:
        return SqliteEventLog(self.db).recent()


def make_world(db: Database, *attempts: Attempt) -> World:
    limits = Limits()
    connector = FakeConnector(list(attempts))
    keys = FakeKeys()
    key = keys.add("id_ed25519")
    known = StubKnownHosts(KnownHostsMatch(trusted=("ssh-ed25519 AAAA",)))
    wizard = InstallWizard(
        connector,  # type: ignore[arg-type]
        SqliteConnectionsRepo(db, limits),
        keys,  # type: ignore[arg-type]
        SqliteEventLog(db),
        groups=SqliteGroupsRepo(db, limits),
        known_hosts=known,
        host_keys=SqliteHostKeyStore(db),
    )
    return World(wizard, connector, keys, key, db, known)


def start(**kw: Any) -> StartMsg:
    values: dict[str, Any] = {
        "type": "start",
        "host": "192.168.10.42",
        "port": 22,
        "username": "john",
        "name": "web-prod",
        "tags": ["prod"],
        "color": "red",
        "notes": "rack 3",
    }
    values.update(kw)
    return StartMsg.model_validate(values)


async def run(
    w: World, msg: StartMsg | None = None, actions: list[WizardAction] | None = None
) -> tuple[Any, str, FakeIO, FakePrompter]:
    io = FakeIO(actions, w.connector.timeline)
    prompter = FakePrompter(password=PASSWORD, hostkey_new=True, kbdint=[["492039"]])
    record, outcome = await w.wizard.run(msg or start(), prompter, io)
    return record, outcome, io, prompter


async def run_fails(
    w: World, msg: StartMsg | None = None, actions: list[WizardAction] | None = None
) -> tuple[BaseException, FakeIO]:
    io = FakeIO(actions, w.connector.timeline)
    prompter = FakePrompter(password=PASSWORD, hostkey_new=True)
    try:
        await w.wizard.run(msg or start(), prompter, io)
    except Exception as exc:
        return exc, io
    pytest.fail("the run was expected to fail but finished")
