"""The browser-facing defences against the real app (docs/architecture.md section 4).

- A page on another origin cannot open the terminal WebSocket or send a mutating request.
- A Host header the app does not serve (DNS rebinding) is refused, from the browser too.
- Every page carries the CSP; the terminal specs check that nothing violates it.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection, connect

from portkey.security.headers import CSP
from tests.e2e.harness.app_api import AppApi


@contextmanager
def terminal_socket(base_url: str, origin: str) -> Iterator[ClientConnection]:
    """/ws/terminal as a browser on ``origin`` would open it: the PC's URL and Host, the TCP
    connection made to wherever the app really is (``$PORTKEY_CONNECT_TO``)."""
    shown = urlsplit(base_url).netloc
    host, port = (os.environ.get("PORTKEY_CONNECT_TO") or shown).rsplit(":", 1)
    sock = socket.create_connection((host, int(port)), timeout=10)
    ws_url = f"ws://{shown}/ws/terminal"
    with connect(ws_url, sock=sock, origin=origin, open_timeout=10) as ws:  # type: ignore[arg-type]
        yield ws


def test_evil_origin_cannot_open_the_websocket(
    evil_origin: Callable[..., dict[str, Any]], base_url: str, app_token: str
) -> None:
    ws_url = base_url.replace("http://", "ws://") + "/ws/terminal"
    result = evil_origin(ws_url=ws_url, api_url=base_url, token=app_token)
    assert result["ws"]["url"] == ws_url
    assert result["ws"]["opened"] is False  # the handshake was refused
    assert result["ws"]["messages"] == []
    assert result["ws"]["closeCode"] == 1006
    assert result["fetch"]["blocked"] is True


def test_handshake_from_another_origin_is_403(base_url: str) -> None:
    with (
        pytest.raises(InvalidStatus) as refused,
        terminal_socket(base_url, "http://evil-origin:8080"),
    ):
        pass
    assert refused.value.response.status_code == 403


def test_own_origin_opens_but_needs_a_token(base_url: str) -> None:
    with terminal_socket(base_url, base_url) as ws:
        ws.send(json.dumps({"type": "auth", "token": "not-a-token"}))
        error = json.loads(ws.recv(timeout=10))
        assert (error["type"], error["code"], error["close"]) == ("error", "unauthorized", 4401)
        with pytest.raises(ConnectionClosed) as closed:
            ws.recv(timeout=10)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == 4401


@pytest.mark.parametrize(
    "host", ["app:8822", "evil.example:8822", "localhost:8000", "0.0.0.0:8822"]
)
def test_wrong_host_header_is_refused(app_api: AppApi, host: str) -> None:
    for path in ("/api/health", "/", "/api/auth/status"):
        r = app_api.raw("GET", path, host=host)
        assert r.status_code == 421, (host, path)
        if path.startswith("/api"):
            assert r.json()["error"]["code"] == "host_not_allowed"


def test_rebound_name_in_the_browser_gets_no_app(page: Page) -> None:
    # A rebinding page would reach the app under its own name; the guard answers 421 and no
    # script loads.
    response = page.goto("http://app:8822/")
    assert response is not None
    assert response.status == 421
    assert page.locator("script").count() == 0


def test_mutating_requests_need_origin_and_csrf_header(app_api: AppApi, app_token: str) -> None:
    body = {"name": "never-made", "host": "sshd", "username": "keyonly", "auth_method": "key"}
    foreign = app_api.request(
        "POST",
        "/api/connections",
        token=app_token,
        json=body,
        headers={"Origin": "http://evil-origin:8080"},
    )
    assert (foreign.status_code, foreign.json()["error"]["code"]) == (403, "origin_not_allowed")
    no_header = app_api.request(
        "POST", "/api/connections", token=app_token, json=body, headers={"X-Port-Key": ""}
    )
    assert no_header.status_code == 403
    names = [
        c["name"]
        for c in app_api.request("GET", "/api/connections", token=app_token).json()["connections"]
    ]
    assert "never-made" not in names


def test_every_page_carries_the_csp(app_api: AppApi) -> None:
    for path in ("/", "/index.html", "/js/main.js", "/api/health"):
        r = app_api.request("GET", path)
        assert r.headers["content-security-policy"] == CSP, path
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
