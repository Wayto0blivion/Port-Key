"""ws_common: the auth handshake, framing and error messages over a fake socket."""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

from portkey.api.schemas.ws import ExitMsg, StatusMsg
from portkey.api.ws_common import (
    INTERNAL_MESSAGE,
    authenticate,
    dump,
    error_message,
    hop_out,
    parse_message,
    send_error,
)
from portkey.config import Limits
from portkey.ssh.errors import (
    AuthFailed,
    BadMessage,
    ConnectionRefused,
    JumpFailed,
    PasswordNotAllowed,
    PromptCancelled,
)
from tests.unit.ssh.fakes import FakeWs, hop

TOKEN = "test-token-not-a-secret"


def text(data: Any) -> dict[str, Any]:
    return {
        "type": "websocket.receive",
        "text": data if isinstance(data, str) else json.dumps(data),
    }


class FakeTokens:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def verify(self, token: str) -> bool:
        self.seen.append(token)
        return token == TOKEN


def services(auth_timeout: float = 5.0) -> Any:
    limits = replace(Limits(), ws_auth_timeout=auth_timeout)
    return SimpleNamespace(config=SimpleNamespace(limits=limits), tokens=FakeTokens())


# --- authenticate ---------------------------------------------------------------------------


async def test_valid_token_sends_nothing() -> None:
    ws, svc = FakeWs(text({"type": "auth", "token": TOKEN})), services()
    assert await authenticate(ws, svc) is True  # type: ignore[arg-type]
    assert ws.sent == []
    assert ws.closed is None
    assert svc.tokens.seen == [TOKEN]


@pytest.mark.parametrize(
    "first",
    [
        text({"type": "auth", "token": "wrong"}),
        text(
            {
                "type": "open",
                "connection_id": "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a",
                "cols": 80,
                "rows": 24,
            }
        ),
        text({"type": "auth", "token": TOKEN, "extra": 1}),
        text("not json"),
        {"type": "websocket.receive", "bytes": b"\x00"},
    ],
)
async def test_anything_but_a_valid_auth_is_unauthorized(first: dict[str, Any]) -> None:
    ws = FakeWs(first)
    assert await authenticate(ws, services()) is False  # type: ignore[arg-type]
    assert ws.sent == [
        {"type": "error", "code": "unauthorized", "message": "Sign in to Port-Key.", "close": 4401}
    ]
    assert ws.closed == 4401


async def test_no_auth_in_time() -> None:
    ws = FakeWs()
    assert await authenticate(ws, services(auth_timeout=0.05)) is False  # type: ignore[arg-type]
    assert ws.sent[0]["code"] == "auth_timeout"
    assert ws.sent[0]["close"] == ws.closed == 4401


async def test_client_gone_before_auth() -> None:
    ws = FakeWs({"type": "websocket.disconnect", "code": 1001})
    assert await authenticate(ws, services()) is False  # type: ignore[arg-type]
    assert ws.sent == []
    assert ws.closed is None


async def test_tokens_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    for token in (TOKEN, "wrong-token-value"):
        ws = FakeWs(text({"type": "auth", "token": token}))
        await authenticate(ws, services())  # type: ignore[arg-type]
    assert TOKEN not in caplog.text
    assert "wrong-token-value" not in caplog.text


def test_handshake_through_a_real_websocket() -> None:
    app = FastAPI()
    svc = services()

    @app.websocket("/ws")
    async def endpoint(ws: WebSocket) -> None:
        if await authenticate(ws, svc):
            await ws.send_json({"type": "pong"})
            await ws.close()

    with warnings.catch_warnings():
        # Starlette 1.x warns that its test client wants httpx2; irrelevant here.
        warnings.simplefilter("ignore")
        from starlette.testclient import TestClient

        client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json() == {"type": "pong"}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        assert ws.receive_json()["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401


# --- framing --------------------------------------------------------------------------------------


def test_required_nullable_fields_stay_and_optional_ones_go() -> None:
    assert dump(ExitMsg(exit_status=None, signal=None, reason="lost")) == {
        "type": "exit",
        "exit_status": None,
        "signal": None,
        "reason": "lost",
    }
    status = dump(StatusMsg(phase="connecting", hop=hop_out(hop(connection_id=None))))
    assert status["hop"]["connection_id"] is None
    assert "method" not in status
    assert "key_name" not in status


def test_parse_message_never_echoes_the_frame() -> None:
    secret = "hunter2-password"
    frame = json.dumps({"type": "prompt_reply", "id": "p1", "value": secret, "bogus": True})
    with pytest.raises(BadMessage) as bad:
        parse_message(frame)
    assert secret not in str(bad.value)
    assert secret not in repr(bad.value)
    assert bad.value.__cause__ is None
    assert bad.value.__suppress_context__
    reply = parse_message(json.dumps({"type": "prompt_reply", "id": "p1", "value": secret}))
    assert secret not in repr(reply)


# --- error messages -------------------------------------------------------------------------


def test_auth_failed_carries_methods_and_hop() -> None:
    msg = dump(
        error_message(AuthFailed("web-prod refused your key.", methods=["publickey"], hop=hop()))
    )
    assert msg["code"] == "auth_failed"
    assert msg["methods"] == ["publickey"]
    assert msg["close"] == 4502
    assert msg["hop"]["name"] == "web-prod"
    assert "cause" not in msg


def test_password_not_allowed_carries_methods() -> None:
    msg = dump(error_message(PasswordNotAllowed("no passwords", methods=["publickey"])))
    assert msg["methods"] == ["publickey"]
    assert "hop" not in msg


def test_jump_failed_carries_cause_and_hop() -> None:
    jump = hop(0, 2, name="build-box")
    msg = dump(error_message(JumpFailed(ConnectionRefused("refused", hop=jump))))
    assert (msg["code"], msg["cause"], msg["message"]) == ("jump_failed", "refused", "refused")
    assert msg["hop"]["role"] == "jump"


def test_cancel_closes_normally() -> None:
    msg = dump(error_message(PromptCancelled()))
    assert (msg["code"], msg["close"]) == ("cancelled", 1000)


async def test_unexpected_errors_are_generic(caplog: pytest.LogCaptureFixture) -> None:
    ws = FakeWs()
    await ws.accept()
    leak = RuntimeError("password=hunter2 leaked into a message")
    await send_error(ws, leak)  # type: ignore[arg-type]
    assert ws.sent == [
        {"type": "error", "code": "internal", "message": INTERNAL_MESSAGE, "close": 1011}
    ]
    assert ws.closed == 1011
    assert "hunter2" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_send_error_on_a_closed_socket_is_a_no_op() -> None:
    ws = FakeWs()
    await ws.accept()
    await ws.close(1000)
    await send_error(ws, PromptCancelled())  # type: ignore[arg-type]
    assert ws.sent == []
