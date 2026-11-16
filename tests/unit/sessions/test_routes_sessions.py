"""GET /api/sessions and DELETE /api/sessions/{id} through the ASGI transport."""

from __future__ import annotations

import httpx

from portkey.models import ConnectionDraft
from portkey.services import Services
from tests.fixtures.prompter import FakePrompter
from tests.unit.sessions.fakes import FakeConnector, RecordingSink


async def open_session(services: Services) -> tuple[str, FakeConnector, str]:
    conn = services.connections.create(
        ConnectionDraft(
            name="web-prod", host="web.example.test", username="john", auth_method="key"
        )
    )
    connector = FakeConnector()
    connector.known[conn.id] = conn.name
    services.sessions.connector = connector
    session = await services.sessions.create(conn.id, 80, 24, FakePrompter(), False)
    return session.session_id, connector, conn.id


async def test_list_shows_live_sessions(
    authed_client: httpx.AsyncClient, started: Services
) -> None:
    assert (await authed_client.get("/api/sessions")).json() == {"sessions": []}
    session_id, _, conn_id = await open_session(started)
    sink = RecordingSink()
    await started.sessions.get(session_id).attach(sink, None)
    r = await authed_client.get("/api/sessions")
    assert r.status_code == 200
    [item] = r.json()["sessions"]
    assert item["session_id"] == session_id
    assert item["connection_id"] == conn_id
    assert item["attached"] is True
    assert item["detached_at"] is None
    assert item["started_at"].endswith("Z") or item["started_at"].endswith("+00:00")
    started.sessions.get(session_id).detach(sink)
    [item] = (await authed_client.get("/api/sessions")).json()["sessions"]
    assert item["attached"] is False
    assert item["detached_at"] is not None


async def test_delete_kills_the_session(
    authed_client: httpx.AsyncClient, started: Services
) -> None:
    session_id, connector, _ = await open_session(started)
    sink = RecordingSink()
    await started.sessions.get(session_id).attach(sink, None)
    r = await authed_client.request("DELETE", f"/api/sessions/{session_id}", json={})
    assert r.status_code == 204
    assert connector.chains[0].close_calls == 1
    await sink.wait_for(lambda: "exit" in sink.kinds())
    assert sink.first("exit") == (None, None, "killed")
    assert (await authed_client.get("/api/sessions")).json() == {"sessions": []}
    again = await authed_client.request("DELETE", f"/api/sessions/{session_id}", json={})
    assert again.status_code == 404
    assert again.json() == {"error": {"code": "session_gone", "message": "That session has ended."}}


async def test_both_routes_need_a_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/sessions")).status_code == 401
    r = await client.request("DELETE", "/api/sessions/s_x", json={})
    assert r.status_code == 401
