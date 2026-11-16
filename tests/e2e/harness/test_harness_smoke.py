"""Smoke tests for the e2e harness itself (package 7). The real specs are package 9's.

These prove the plumbing: the app container answers, Chromium loads it, the evil-origin page
runs its attack and reports back, and the network layout is what the specs assume. They make
no claim about whether the app blocks the attack; that is package 9's ``evil-origin`` spec.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.harness.app_api import AppApi
from tests.fixtures import testbed as tb


def test_app_is_healthy(app_api: AppApi) -> None:
    body = app_api.wait_healthy(timeout=5)
    assert body["status"] == "ok"
    assert body["version"]


def test_chromium_loads_the_app(page: Page, base_url: str) -> None:
    response = page.goto(base_url + "/")
    assert response is not None
    assert response.ok
    assert page.locator('script[type="module"][src="js/main.js"]').count() == 1


def test_evil_origin_page_reports_its_attempt(
    evil_origin: Callable[..., dict[str, Any]], evil_url: str
) -> None:
    result = evil_origin(timeout_ms=3000)
    assert result["origin"] == evil_url
    assert result["done"] is True
    assert result["ws"]["url"] == "ws://app:8000/ws/terminal"
    assert result["ws"]["done"] is True
    assert result["fetch"]["done"] is True
    assert isinstance(result["fetch"]["blocked"], bool)


@pytest.mark.parametrize(
    "server", [tb.SSHD, tb.SSHD_ALT_PORT, tb.SSHD_REKEYED], ids=lambda s: f"{s.host}:{s.port}"
)
def test_sshd_is_on_the_e2e_network(server: tb.SshServer) -> None:
    with socket.create_connection((server.host, server.port), timeout=5) as sock:
        assert sock.recv(64).startswith(b"SSH-2.0-")


def test_sshd_inner_is_not_on_the_e2e_network() -> None:
    with pytest.raises((socket.gaierror, TimeoutError, ConnectionRefusedError)):
        socket.create_connection((tb.SSHD_INNER.host, 22), timeout=3).close()
