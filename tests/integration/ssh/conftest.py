"""Fixtures for package 4's integration tests: the real ``ChainConnector`` against the sshd
test bed, with an app database and the fixture .ssh copy from ``portkey_config``. Helpers that
are not fixtures live in ``helpers.py``.

- ``svc``: ``Services`` built from ``portkey_config`` (migrated database, real key provider and
  known_hosts reader over the 0777 fixture copy). ``svc_with(**limits)`` builds one with other
  ``Limits``.
- ``write_known_hosts(lines)``: replace the fixture copy's known_hosts.
- ``chains``: a list; chains appended to it are closed at teardown even if the test fails.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from portkey.config import Config, Limits
from portkey.services import Services, build_services
from portkey.ssh.chain import OpenChain


def _build(config: Config) -> Services:
    services = build_services(config)
    services.db.migrate()
    return services


@pytest.fixture
def svc(portkey_config: Config) -> Iterator[Services]:
    services = _build(portkey_config)
    try:
        yield services
    finally:
        services.db.close()


@pytest.fixture
def svc_with(portkey_config: Config) -> Iterator[Callable[..., Services]]:
    built: list[Services] = []

    def make(**limits: Any) -> Services:
        services = _build(replace(portkey_config, limits=replace(Limits(), **limits)))
        built.append(services)
        return services

    yield make
    for services in built:
        services.db.close()


@pytest.fixture
def write_known_hosts(fixture_ssh_dir: Path) -> Callable[[list[str]], None]:
    def write(lines: list[str]) -> None:
        (fixture_ssh_dir / "known_hosts").write_text("\n".join(lines) + "\n")

    return write


@pytest.fixture
async def chains() -> AsyncIterator[list[OpenChain]]:
    opened: list[OpenChain] = []
    yield opened
    for chain in opened:
        await chain.close()
