"""Helpers for the first-visit setup code tests: read the code from captured log records, as
the owner reads it from ``docker compose logs portkey``, and run an app through its startup."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import httpx

from portkey.app import create_app, services_of
from portkey.config import Config
from portkey.security.setup_code import LOG_LINE
from portkey.services import Services

BASE_URL = "http://localhost:8822"


def codes_in_log(records: Iterable[logging.LogRecord]) -> list[str]:
    """Every setup code the records show, oldest first (as rendered, after redaction)."""
    return LOG_LINE.findall("\n".join(r.getMessage() for r in records))


@asynccontextmanager
async def running(config: Config) -> AsyncIterator[tuple[Services, httpx.AsyncClient]]:
    """One start of the app on ``config`` (startup prints the code when there is no password),
    with a client that sends what the browser sends."""
    app = create_app(config)
    services = services_of(app)
    await services.startup()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE_URL,
            headers={"Origin": BASE_URL, "X-Port-Key": "1"},
        ) as client:
            yield services, client
    finally:
        await services.shutdown()
