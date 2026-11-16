"""Host and Origin on WebSocket handshakes (docs/ws-protocol.md "Transport").

A refused handshake must be closed *before* accept: the server then answers the HTTP upgrade
with 403 and the WebSocket handler never runs, so a cross-site page cannot even reach the auth
message (cross-site WebSocket hijacking).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect

from portkey.config import Config, default_allowed_hosts
from portkey.security.hostguard import HostGuardMiddleware

OK_HOST = ("host", "localhost:8822")
OK_ORIGIN = ("origin", "http://localhost:8822")


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_hosts=default_allowed_hosts(8822))


def _refused_before_accept(sent: list[dict[str, Any]]) -> bool:
    return [m["type"] for m in sent] == ["websocket.close"] and sent[0]["code"] == 4403


@pytest.mark.parametrize("path", ["/ws/terminal", "/ws/install"])
@pytest.mark.parametrize(
    "origin",
    [
        None,  # non-browser client or stripped header
        "null",  # sandboxed iframe, file:// page
        "http://localhost:3000",  # another localhost origin on a different port
        "http://127.0.0.1:3000",
        "http://evil.example",  # malicious site in the same browser
        "https://localhost:8822",
        "http://localhost:8822/",
    ],
)
async def test_cross_origin_handshake_refused_before_accept(
    ws_call: Any, spy: Any, cfg: Config, path: str, origin: str | None
) -> None:
    headers = [OK_HOST] + ([("origin", origin)] if origin is not None else [])
    sent = await ws_call(HostGuardMiddleware(spy, cfg), path, headers)
    assert _refused_before_accept(sent)
    assert spy.calls == 0  # the handler never ran


@pytest.mark.parametrize("host", [None, "evil.example:8822", "localhost:8000", "localhost"])
async def test_bad_host_handshake_refused_before_accept(
    ws_call: Any, spy: Any, cfg: Config, host: str | None
) -> None:
    headers = [OK_ORIGIN] + ([("host", host)] if host is not None else [])
    sent = await ws_call(HostGuardMiddleware(spy, cfg), "/ws/terminal", headers)
    assert _refused_before_accept(sent)
    assert spy.calls == 0


async def test_dns_rebinding_handshake_refused(ws_call: Any, spy: Any, cfg: Config) -> None:
    # After rebinding, the attacker's page is same-origin with its own name: Host and Origin
    # both say evil.example, and both are refused.
    headers = [("host", "evil.example:8822"), ("origin", "http://evil.example:8822")]
    sent = await ws_call(HostGuardMiddleware(spy, cfg), "/ws/terminal", headers)
    assert _refused_before_accept(sent)


async def test_duplicate_origin_on_handshake_refused(ws_call: Any, spy: Any, cfg: Config) -> None:
    headers = [OK_HOST, OK_ORIGIN, ("origin", "http://evil.example")]
    sent = await ws_call(HostGuardMiddleware(spy, cfg), "/ws/terminal", headers)
    assert _refused_before_accept(sent)


@pytest.mark.parametrize("host", ["localhost:8822", "127.0.0.1:8822", "[::1]:8822"])
async def test_allowed_handshake_reaches_handler(
    ws_call: Any, spy: Any, cfg: Config, host: str
) -> None:
    headers = [("host", host), ("origin", f"http://{host}")]
    sent = await ws_call(HostGuardMiddleware(spy, cfg), "/ws/terminal", headers)
    assert spy.calls == 1
    assert sent[0]["type"] == "websocket.accept"


async def test_handshake_needs_no_csrf_headers(ws_call: Any, spy: Any, cfg: Config) -> None:
    # Browsers cannot set custom headers on a WebSocket; Origin is the defence there, and the
    # token travels in the first message.
    sent = await ws_call(HostGuardMiddleware(spy, cfg), "/ws/install", [OK_HOST, OK_ORIGIN])
    assert sent[0]["type"] == "websocket.accept"


@pytest.mark.parametrize("path", ["/ws/terminal", "/ws/install"])
async def test_real_app_refuses_cross_origin_handshake_before_accept(
    ws_call: Any, app: FastAPI, path: str
) -> None:
    sent = await ws_call(app, path, [OK_HOST, ("origin", "http://localhost:3000")])
    assert _refused_before_accept(sent)


def test_browser_sees_handshake_rejected(app: FastAPI) -> None:
    """End to end through Starlette's test client: the upgrade is rejected, never opened."""
    with warnings.catch_warnings():
        # Starlette 1.x warns that its test client wants httpx2; irrelevant to this check.
        warnings.simplefilter("ignore")
        from starlette.testclient import TestClient

        client = TestClient(app, base_url="http://localhost:8822")
    evil = {"Origin": "http://evil.example"}
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/ws/terminal", headers=evil),
    ):
        pytest.fail("handshake was accepted")
    assert refused.value.code == 4403
