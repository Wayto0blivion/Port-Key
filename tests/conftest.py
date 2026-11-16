"""Shared fixtures (package 0). Package-specific fixtures go in each test directory's own
conftest.py, never here; ask the package-0 owner for changes to this file.

- ``config``: a ``Config`` whose data, .ssh and generated-keys dirs are empty temp dirs.
- ``app``: ``create_app(config)``; ``services``: its ``Services``.
- ``started``: runs ``services.startup()``/``shutdown()`` (httpx's ASGI transport does not run
  the lifespan).
- ``client``: httpx client over ASGI with an allowed Host and Origin and ``X-Port-Key: 1``.
- ``authed_client``: same, with ``require_auth`` overridden so routes run without package 1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from portkey.api.deps import require_auth
from portkey.app import create_app, services_of
from portkey.config import Config, default_allowed_hosts
from portkey.services import Services

BASE_URL = "http://localhost:8822"
TEST_TOKEN = "test-token-not-a-secret"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    data, ssh, keys = tmp_path / "data", tmp_path / "ssh", tmp_path / "keys-generated"
    for d in (data, ssh, keys):
        d.mkdir()
    return Config(
        data_dir=data,
        ssh_dir=ssh,
        keys_dir=keys,
        ssh_dir_display="C:\\Users\\john\\.ssh",
        keys_dir_display="C:\\Users\\john\\.ssh\\port-key",
        public_port=8822,
        allowed_hosts=default_allowed_hosts(8822),
    )


@pytest.fixture
def app(config: Config) -> FastAPI:
    return create_app(config)


@pytest.fixture
def services(app: FastAPI) -> Services:
    return services_of(app)


@pytest.fixture
async def started(services: Services) -> AsyncIterator[Services]:
    await services.startup()
    try:
        yield services
    finally:
        await services.shutdown()


def _client(app: FastAPI, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers={"Origin": BASE_URL, "X-Port-Key": "1", **headers},
    )


@pytest.fixture
async def client(app: FastAPI, started: Services) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(app) as c:
        yield c


@pytest.fixture
async def authed_client(app: FastAPI, started: Services) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[require_auth] = lambda: TEST_TOKEN
    try:
        async with _client(app, Authorization=f"Bearer {TEST_TOKEN}") as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)
