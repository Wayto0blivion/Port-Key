"""The wizard through a jump host, and one whole run through /ws/install on the real app."""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

from portkey.app import create_app, services_of
from portkey.config import Config
from portkey.models import ConnectionDraft
from portkey.services import Services
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter
from tests.integration.wizard.helpers import installed_line, remote_keys, reset, run_ok, start

Cleanup = list[tuple[str, tb.SshServer, tb.SshServer | None]]


async def test_install_through_the_jump_host(svc: Services, cleanup: Cleanup) -> None:
    cleanup.append(("fresh", tb.SSHD_INNER, tb.SSHD))
    jump = svc.connections.create(
        ConnectionDraft(name="jump", host="sshd", username="keyonly", auth_method="key")
    )
    msg = start("fresh", host="sshd-inner", name="inner-fresh", jump_id=jump.id)
    prompter = FakePrompter(password=tb.USERS["fresh"].password)
    record, outcome, io = await run_ok(svc, msg, prompter)
    assert outcome == "installed"
    connect = [(s.state, s.hop.index) for s in io.steps if s.step == "connect" and s.hop]
    assert connect == [("running", 0), ("ok", 0), ("running", 1), ("ok", 1)]
    assert [c.hop.index for c in prompter.calls] == [1]  # only the target asked for a password
    assert svc.connections.get(record.id).jump_id == jump.id
    text, modes = await remote_keys("fresh", server=tb.SSHD_INNER, via=tb.SSHD)
    assert text == installed_line(svc) + "\n"
    assert modes == "700 600"


def test_whole_run_through_the_socket(portkey_config: Config) -> None:
    """Real app, real token, real sshd: start, a password prompt, steps, done."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Starlette's test client warns about httpx versions
        from starlette.testclient import TestClient

        app = create_app(portkey_config)
        client = TestClient(app, base_url="http://localhost:8822")
    services = services_of(app)
    password = tb.USERS["fresh"].password
    sent: list[dict[str, Any]] = []
    with client:
        token = services.auth.setup("TEST-ONLY app password").token
        headers = {"host": "localhost:8822", "origin": "http://localhost:8822"}
        with client.websocket_connect("/ws/install", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.send_json(start("fresh").model_dump(mode="json"))
            while True:
                msg = ws.receive_json()
                sent.append(msg)
                if msg["type"] == "prompt":
                    assert msg["kind"] == "password"
                    ws.send_json({"type": "prompt_reply", "id": msg["id"], "value": password})
                if msg["type"] in ("done", "error"):
                    break
        try:
            assert sent[-1]["type"] == "done", sent[-1]
            assert sent[-1]["outcome"] == "installed"
            assert sent[-1]["connection"]["auth_method"] == "key"
            assert password not in repr(sent)
            steps = [(m["step"], m["state"]) for m in sent if m["type"] == "step"]
            assert ("discard_password", "ok") in steps
        finally:
            asyncio.run(reset("fresh", tb.SSHD, None))
