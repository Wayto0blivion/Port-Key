"""Smoke tests for the skeleton: the app boots, routes are wired, stubs answer 501."""

from __future__ import annotations

import importlib
import pkgutil

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

import portkey
from portkey.app import ROUTERS
from portkey.config import Config

# Every endpoint in docs/api.md and docs/ws-protocol.md.
HTTP_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
    ("GET", "/api/connections"),
    ("POST", "/api/connections"),
    ("POST", "/api/connections/bulk"),
    ("POST", "/api/connections/reachability"),
    ("PATCH", "/api/connections/{conn_id}"),
    ("DELETE", "/api/connections/{conn_id}"),
    ("POST", "/api/connections/{conn_id}/duplicate"),
    ("GET", "/api/groups"),
    ("POST", "/api/groups"),
    ("PATCH", "/api/groups/{group_id}"),
    ("DELETE", "/api/groups/{group_id}"),
    ("GET", "/api/tags"),
    ("PATCH", "/api/tags/{name}"),
    ("DELETE", "/api/tags/{name}"),
    ("GET", "/api/keys"),
    ("GET", "/api/keys/name-check"),
    ("POST", "/api/keys/generate"),
    ("POST", "/api/keys/lock"),
    ("GET", "/api/keys/{key_id}/public"),
    ("PATCH", "/api/keys/{key_id}"),
    ("POST", "/api/keys/{key_id}/unlock"),
    ("GET", "/api/hosts/suggestions"),
    ("POST", "/api/hosts/suggestions/dismiss"),
    ("POST", "/api/hosts/suggestions/restore"),
    ("GET", "/api/hostkeys"),
    ("DELETE", "/api/hostkeys/{key_id}"),
    ("GET", "/api/sessions"),
    ("DELETE", "/api/sessions/{session_id}"),
    ("GET", "/api/settings"),
    ("PATCH", "/api/settings"),
    ("GET", "/api/diag"),
    ("POST", "/api/diag/resolve"),
}
WS_ROUTES = {"/ws/terminal", "/ws/install"}


def test_every_module_imports() -> None:
    names = [m.name for m in pkgutil.walk_packages(portkey.__path__, "portkey.")]
    assert "portkey.api.schemas.ws" in names
    for name in names:
        importlib.import_module(name)


def test_every_documented_route_is_registered(app: FastAPI) -> None:
    # FastAPI includes routers lazily, so read the HTTP routes from the OpenAPI schema.
    http = {
        (method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item
    }
    assert http >= HTTP_ROUTES, HTTP_ROUTES - http
    assert http - HTTP_ROUTES == set(), "route missing from docs/api.md and this list"
    ws = {r.path for router in ROUTERS for r in router.routes if isinstance(r, APIWebSocketRoute)}
    assert ws == WS_ROUTES


async def test_health(client: httpx.AsyncClient, config: Config) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": config.version}


async def test_unknown_api_path_is_json_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not found."}}


async def test_index_is_served(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<script type="module" src="js/main.js"></script>' in r.text


# Routes that were stubs when this list was last updated (package 5 owns both). The test uses the
# first one that still answers 501 and skips once every package has landed.
STUB_CANDIDATES = (("GET", "/api/sessions"), ("DELETE", "/api/sessions/s_not-a-session"))


async def test_stub_route_answers_501_envelope(authed_client: httpx.AsyncClient) -> None:
    for method, path in STUB_CANDIDATES:
        body = None if method == "GET" else {}
        r = await authed_client.request(method, path, json=body)
        if r.status_code == 501:
            assert r.json() == {
                "error": {
                    "code": "not_implemented",
                    "message": "This part of Port-Key is not built yet.",
                }
            }
            return
    pytest.skip("every route is implemented; no stub left to check")


async def test_openapi_lists_schemas(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    schemas = r.json()["components"]["schemas"]
    for name in ("Connection", "Group", "KeyInfo", "SettingsOut", "DiagReport"):
        assert name in schemas
