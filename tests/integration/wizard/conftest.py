"""Fixtures for package 6's integration tests: the real wizard (``services.wizard``, wired as
the app wires it) against the sshd test bed. Helpers that are not fixtures are in
``helpers.py``.

- ``svc``: ``Services`` built from ``portkey_config`` (migrated database, the 0777 fixture
  .ssh copy, whose known_hosts trusts sshd, [sshd]:2222 and sshd-inner).
- ``cleanup``: a list of ``(user, server, tunnel_via)``; each is put back with
  ``helpers.reset`` at teardown, even when the test failed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from portkey.config import Config
from portkey.services import Services, build_services
from tests.fixtures import testbed as tb
from tests.integration.wizard.helpers import reset


@pytest.fixture
def svc(portkey_config: Config) -> Iterator[Services]:
    services = build_services(portkey_config)
    services.db.migrate()
    try:
        yield services
    finally:
        services.db.close()


@pytest.fixture
async def cleanup() -> AsyncIterator[list[tuple[str, tb.SshServer, tb.SshServer | None]]]:
    users: list[tuple[str, tb.SshServer, tb.SshServer | None]] = []
    yield users
    for user, server, via in users:
        await reset(user, server, via)
