"""/api/auth/* through the real app, and the bearer-token matrix on protected routes."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from portkey.services import Services

PW = "correct horse battery"
DAY = 24 * 3600.0


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client: httpx.AsyncClient) -> str:
    r = await client.post("/api/auth/setup", json={"password": PW})
    assert r.status_code == 200, r.text
    return str(r.json()["token"])


class FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- status / setup / login / logout -------------------------------------------------------------


async def test_status_before_and_after_setup(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/status")
    assert r.json() == {"setup": False, "authenticated": False}
    token = await _setup(client)
    r = await client.get("/api/auth/status")
    assert r.json() == {"setup": True, "authenticated": False}
    r = await client.get("/api/auth/status", headers=bearer(token))
    assert r.json() == {"setup": True, "authenticated": True}
    r = await client.get("/api/auth/status", headers=bearer("x" * 43))
    assert r.json() == {"setup": True, "authenticated": False}


async def test_setup_response_shape(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/setup", json={"password": PW})
    body = r.json()
    assert set(body) == {"token", "expires_at"}
    assert body["expires_at"].endswith("Z")
    assert PW not in r.text


async def test_setup_twice_is_409(client: httpx.AsyncClient) -> None:
    await _setup(client)
    r = await client.post("/api/auth/setup", json={"password": "another password"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_setup"


@pytest.mark.parametrize(
    "body", [{"password": "short"}, {"password": "x" * 1025}, {}, {"password": PW, "admin": True}]
)
async def test_setup_validation(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    r = await client.post("/api/auth/setup", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"
    status = await client.get("/api/auth/status")
    assert status.json()["setup"] is False


async def test_login_before_setup_is_409(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_setup"


async def test_login_good_and_bad(client: httpx.AsyncClient) -> None:
    await _setup(client)
    r = await client.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 200
    assert (await client.get("/api/auth/status", headers=bearer(r.json()["token"]))).json()[
        "authenticated"
    ]
    r = await client.post("/api/auth/login", json={"password": "wrong-guess-123"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "bad_password"
    assert "wrong-guess-123" not in r.text


async def test_login_rate_limited_with_retry_after(
    client: httpx.AsyncClient, services: Services
) -> None:
    await _setup(client)
    clock = FakeClock(500.0)
    services.auth.clock = clock  # type: ignore[attr-defined]
    for _ in range(3):
        r = await client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 429
    body = r.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["retry_after"] == 5
    assert "5 seconds" in body["message"]
    clock.advance(5)
    r = await client.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 200


async def test_logout_revokes_this_token_only(client: httpx.AsyncClient) -> None:
    a = await _setup(client)
    b = (await client.post("/api/auth/login", json={"password": PW})).json()["token"]
    r = await client.post("/api/auth/logout", json={}, headers=bearer(a))
    assert r.status_code == 204
    assert r.content == b""
    assert (await client.get("/api/auth/status", headers=bearer(a))).json()[
        "authenticated"
    ] is False
    assert (await client.get("/api/auth/status", headers=bearer(b))).json()["authenticated"] is True
    r = await client.post("/api/auth/logout", json={}, headers=bearer(a))
    assert r.status_code == 401


async def test_logout_needs_token(client: httpx.AsyncClient) -> None:
    await _setup(client)
    r = await client.post("/api/auth/logout", json={})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


# --- password change -----------------------------------------------------------------------------


async def test_change_password_revokes_every_other_token(client: httpx.AsyncClient) -> None:
    mine = await _setup(client)
    other = (await client.post("/api/auth/login", json={"password": PW})).json()["token"]
    r = await client.post(
        "/api/auth/password", json={"old": PW, "new": "new password 2026"}, headers=bearer(mine)
    )
    assert r.status_code == 204
    assert (await client.get("/api/auth/status", headers=bearer(mine))).json()["authenticated"]
    r = await client.get("/api/auth/status", headers=bearer(other))
    assert r.json()["authenticated"] is False
    r = await client.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"password": "new password 2026"})
    assert r.status_code == 200


async def test_change_password_wrong_old_is_bad_password(client: httpx.AsyncClient) -> None:
    mine = await _setup(client)
    r = await client.post(
        "/api/auth/password", json={"old": "nope", "new": "new password 2026"}, headers=bearer(mine)
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "bad_password"
    assert (await client.get("/api/auth/status", headers=bearer(mine))).json()["authenticated"]


async def test_change_password_needs_token(client: httpx.AsyncClient) -> None:
    await _setup(client)
    r = await client.post("/api/auth/password", json={"old": PW, "new": "new password 2026"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_change_password_short_new_is_422_without_echo(client: httpx.AsyncClient) -> None:
    mine = await _setup(client)
    r = await client.post(
        "/api/auth/password", json={"old": PW, "new": "tiny"}, headers=bearer(mine)
    )
    assert r.status_code == 422
    assert PW not in r.text
    assert "tiny" not in r.text


# --- the token matrix on protected routes --------------------------------------------------------

PROTECTED = [
    ("GET", "/api/connections"),
    ("GET", "/api/keys"),
    ("GET", "/api/settings"),
    ("GET", "/api/sessions"),
    ("GET", "/api/diag"),
    ("POST", "/api/keys/lock"),
    ("POST", "/api/auth/logout"),
]


async def _call(client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> httpx.Response:
    if method == "GET":
        return await client.get(path, **kw)
    return await client.request(method, path, json={}, **kw)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", "Bearer garbage", "Basic dXNlcjpwYXNz", "Bearer " + "A" * 43],
)
async def test_protected_routes_refuse_without_valid_token(
    client: httpx.AsyncClient, method: str, path: str, authorization: str | None
) -> None:
    await _setup(client)
    headers = {} if authorization is None else {"Authorization": authorization}
    r = await _call(client, method, path, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "Sign in to Port-Key."}}


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_protected_routes_accept_a_valid_token(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    token = await _setup(client)
    r = await _call(client, method, path, headers=bearer(token))
    assert r.status_code != 401


async def test_expired_token_is_signed_out(client: httpx.AsyncClient, services: Services) -> None:
    clock = FakeClock(1_800_000_000.0)
    services.tokens.clock = clock  # type: ignore[attr-defined]
    token = await _setup(client)
    clock.advance(30 * DAY + 1)
    r = await client.get("/api/keys", headers=bearer(token))
    assert r.status_code == 401


async def test_used_token_slides(client: httpx.AsyncClient, services: Services) -> None:
    clock = FakeClock(1_800_000_000.0)
    services.tokens.clock = clock  # type: ignore[attr-defined]
    token = await _setup(client)
    for _ in range(3):
        clock.advance(20 * DAY)
        r = await client.get("/api/auth/status", headers=bearer(token))
        assert r.json()["authenticated"] is True


async def test_open_endpoints_need_no_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/health")).status_code == 200
    assert (await client.get("/api/auth/status")).status_code == 200
    assert (await client.post("/api/auth/login", json={"password": PW})).status_code == 409
