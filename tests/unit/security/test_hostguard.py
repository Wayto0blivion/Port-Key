"""Host / Origin / X-Port-Key / Content-Type matrix for HTTP (docs/api.md "Conventions").

Attackers modelled: a page on another site in the same browser (foreign Origin), another
localhost origin on a different port, DNS rebinding (foreign Host with a loopback address), and
a page that omits or fakes the CSRF headers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI

from portkey.config import Config, default_allowed_hosts, load_config
from portkey.security.hostguard import HostGuardMiddleware, host_allowed, origin_allowed

OK_HOST = ("host", "localhost:8822")
OK_ORIGIN = ("origin", "http://localhost:8822")
CSRF = ("x-port-key", "1")
JSON = ("content-type", "application/json")
MUTATING = [OK_HOST, OK_ORIGIN, CSRF, JSON]


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_hosts=default_allowed_hosts(8822))


# --- host_allowed / origin_allowed ---------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["localhost:8822", "127.0.0.1:8822", "[::1]:8822", "LOCALHOST:8822"]
)
def test_allowed_hosts(cfg: Config, host: str) -> None:
    assert host_allowed(host, cfg)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "localhost",  # no port
        "localhost:8000",  # container port, not the published one
        "localhost.:8822",  # trailing dot
        "evil.example:8822",  # DNS rebinding
        "192.168.10.42:8822",  # LAN address of the PC
        "localhost:8822.evil.example",
        "localhost:8822 ",
        "user@localhost:8822",
        "0.0.0.0:8822",
        "[::ffff:127.0.0.1]:8822",
    ],
)
def test_refused_hosts(cfg: Config, host: str | None) -> None:
    assert not host_allowed(host, cfg)


@pytest.mark.parametrize(
    "origin", ["http://localhost:8822", "http://127.0.0.1:8822", "http://[::1]:8822"]
)
def test_allowed_origins(cfg: Config, origin: str) -> None:
    assert origin_allowed(origin, cfg)


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "http://localhost:3000",  # another localhost origin on a different port
        "https://localhost:8822",  # scheme differs
        "http://localhost:8822/",
        "http://localhost:8822/path",
        "http://evil.example",
        "http://evil.example:8822",
        "http://localhost",
        "http://LOCALHOST:8822",  # browsers never send this; exact match only
        "http://localhost:8822.evil.example",
    ],
)
def test_refused_origins(cfg: Config, origin: str | None) -> None:
    assert not origin_allowed(origin, cfg)


def test_allowed_hosts_override_replaces_defaults() -> None:
    cfg = load_config({"PORTKEY_ALLOWED_HOSTS": "app:8000, Web-Prod:8822"})
    assert host_allowed("app:8000", cfg)
    assert host_allowed("web-prod:8822", cfg)
    assert origin_allowed("http://app:8000", cfg)
    assert not host_allowed("localhost:8822", cfg)
    assert not origin_allowed("http://localhost:8822", cfg)


def test_override_entries_are_exact_not_patterns() -> None:
    cfg = replace(Config(), allowed_hosts=("app:8000",))
    assert not host_allowed("evilapp:8000", cfg)
    assert not host_allowed("app:80001", cfg)


def test_empty_allowlist_refuses_everything() -> None:
    cfg = Config(allowed_hosts=())
    assert not host_allowed("localhost:8822", cfg)
    assert not origin_allowed("http://localhost:8822", cfg)


# --- middleware matrix ---------------------------------------------------------------------------


def _guard(spy: Any, cfg: Config) -> HostGuardMiddleware:
    return HostGuardMiddleware(spy, cfg)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_need_only_host(
    http_call: Any, spy: Any, cfg: Config, method: str
) -> None:
    r = await http_call(_guard(spy, cfg), method, "/api/health", [OK_HOST])
    assert r.status == 200
    assert spy.calls == 1


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"])
@pytest.mark.parametrize("host", [None, "evil.example:8822", "localhost:8000"])
async def test_bad_host_is_421_for_every_method(
    http_call: Any, spy: Any, cfg: Config, method: str, host: str | None
) -> None:
    headers = [h for h in MUTATING if h[0] != "host"]
    if host is not None:
        headers.append(("host", host))
    r = await http_call(_guard(spy, cfg), method, "/api/connections", headers)
    assert r.status == 421
    assert r.json()["error"]["code"] == "host_not_allowed"
    assert spy.calls == 0


async def test_duplicate_host_headers_are_refused(http_call: Any, spy: Any, cfg: Config) -> None:
    headers = [("host", "evil.example:8822"), OK_HOST, OK_ORIGIN, CSRF, JSON]
    r = await http_call(_guard(spy, cfg), "POST", "/api/connections", headers)
    assert r.status == 421
    r = await http_call(_guard(spy, cfg), "GET", "/", [OK_HOST, ("host", "evil.example:8822")])
    assert r.status == 421
    assert spy.calls == 0


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT", "PROPFIND"])
@pytest.mark.parametrize(
    "origin",
    [None, "null", "http://localhost:3000", "http://evil.example", "https://localhost:8822"],
)
async def test_mutating_request_needs_allowed_origin(
    http_call: Any, spy: Any, cfg: Config, method: str, origin: str | None
) -> None:
    headers = [OK_HOST, CSRF, JSON]
    if origin is not None:
        headers.append(("origin", origin))
    r = await http_call(_guard(spy, cfg), method, "/api/connections", headers, b"{}")
    assert r.status == 403
    assert r.json()["error"]["code"] == "origin_not_allowed"
    assert spy.calls == 0


async def test_duplicate_origin_headers_are_refused(http_call: Any, spy: Any, cfg: Config) -> None:
    headers = [*MUTATING, ("origin", "http://evil.example")]
    r = await http_call(_guard(spy, cfg), "POST", "/api/connections", headers, b"{}")
    assert r.status == 403
    assert r.json()["error"]["code"] == "origin_not_allowed"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_request_with_foreign_origin_is_refused(
    http_call: Any, spy: Any, cfg: Config, method: str
) -> None:
    headers = [OK_HOST, ("origin", "http://localhost:3000")]
    r = await http_call(_guard(spy, cfg), method, "/api/keys", headers)
    assert r.status == 403
    assert spy.calls == 0


async def test_safe_request_with_own_origin_passes(http_call: Any, spy: Any, cfg: Config) -> None:
    r = await http_call(_guard(spy, cfg), "GET", "/api/keys", [OK_HOST, OK_ORIGIN])
    assert r.status == 200


@pytest.mark.parametrize("value", [None, "0", "true", "1 ", "11", ""])
async def test_mutating_request_needs_x_port_key_1(
    http_call: Any, spy: Any, cfg: Config, value: str | None
) -> None:
    headers = [OK_HOST, OK_ORIGIN, JSON]
    if value is not None:
        headers.append(("x-port-key", value))
    r = await http_call(_guard(spy, cfg), "POST", "/api/auth/login", headers, b"{}")
    assert r.status == 403
    assert r.json()["error"]["code"] == "missing_csrf_header"
    assert spy.calls == 0


async def test_duplicate_x_port_key_is_refused(http_call: Any, spy: Any, cfg: Config) -> None:
    r = await http_call(_guard(spy, cfg), "POST", "/api/x", [*MUTATING, CSRF], b"{}")
    assert r.status == 403


@pytest.mark.parametrize(
    "ctype",
    [
        None,
        "text/plain",
        "text/plain;charset=UTF-8",  # what fetch() sends for a string body by default
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/json-patch+json",
        "application/jsonx",
        "",
    ],
)
async def test_mutating_request_needs_json(
    http_call: Any, spy: Any, cfg: Config, ctype: str | None
) -> None:
    headers = [OK_HOST, OK_ORIGIN, CSRF]
    if ctype is not None:
        headers.append(("content-type", ctype))
    r = await http_call(_guard(spy, cfg), "DELETE", "/api/connections/x", headers, b"{}")
    assert r.status == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"
    assert spy.calls == 0


@pytest.mark.parametrize(
    "ctype", ["application/json", "application/json; charset=utf-8", "Application/JSON"]
)
async def test_json_content_types_pass(http_call: Any, spy: Any, cfg: Config, ctype: str) -> None:
    headers = [OK_HOST, OK_ORIGIN, CSRF, ("content-type", ctype)]
    r = await http_call(_guard(spy, cfg), "PATCH", "/api/settings", headers, b"{}")
    assert r.status == 200
    assert spy.calls == 1


@pytest.mark.parametrize("host", ["localhost:8822", "127.0.0.1:8822", "[::1]:8822"])
async def test_each_allowed_host_with_its_origin_passes(
    http_call: Any, spy: Any, cfg: Config, host: str
) -> None:
    headers = [("host", host), ("origin", f"http://{host}"), CSRF, JSON]
    r = await http_call(_guard(spy, cfg), "POST", "/api/connections", headers, b"{}")
    assert r.status == 200


async def test_check_order_host_then_origin_then_csrf_then_type(
    http_call: Any, spy: Any, cfg: Config
) -> None:
    guard = _guard(spy, cfg)
    r = await http_call(guard, "POST", "/x", [("host", "evil.example:8822")])
    assert r.status == 421
    r = await http_call(guard, "POST", "/x", [OK_HOST])
    assert r.json()["error"]["code"] == "origin_not_allowed"
    r = await http_call(guard, "POST", "/x", [OK_HOST, OK_ORIGIN])
    assert r.json()["error"]["code"] == "missing_csrf_header"
    r = await http_call(guard, "POST", "/x", [OK_HOST, OK_ORIGIN, CSRF])
    assert r.json()["error"]["code"] == "unsupported_media_type"


async def test_refusal_carries_security_headers_and_no_store(
    http_call: Any, spy: Any, cfg: Config
) -> None:
    r = await http_call(_guard(spy, cfg), "GET", "/", [("host", "evil.example:8822")])
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["content-type"] == "application/json"
    assert "evil.example" not in r.body.decode()


async def test_lifespan_scope_passes_through(spy: Any, cfg: Config) -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    await HostGuardMiddleware(inner, cfg)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]


async def test_refusals_are_logged_without_secrets(
    http_call: Any, spy: Any, cfg: Config, caplog: pytest.LogCaptureFixture
) -> None:
    headers = [("host", "evil.example:8822"), ("authorization", "Bearer abcdefghijklmnopqrstu")]
    with caplog.at_level("WARNING", logger="portkey.security"):
        await http_call(_guard(spy, cfg), "GET", "/api/keys", headers)
    assert "host_not_allowed" in caplog.text
    assert "abcdefghijklmnopqrstu" not in caplog.text


# --- through the real app ------------------------------------------------------------------------


async def test_real_app_dns_rebinding_gets_421(http_call: Any, app: FastAPI, started: Any) -> None:
    r = await http_call(app, "GET", "/api/health", [("host", "evil.example:8822")])
    assert r.status == 421
    r = await http_call(app, "GET", "/", [("host", "evil.example:8822")])
    assert r.status == 421


async def test_real_app_health_needs_allowed_host_only(
    http_call: Any, app: FastAPI, started: Any
) -> None:
    r = await http_call(app, "GET", "/api/health", [OK_HOST])
    assert r.status == 200


async def test_real_app_cross_origin_login_is_refused(
    http_call: Any, app: FastAPI, started: Any
) -> None:
    headers = [OK_HOST, ("origin", "http://localhost:3000"), CSRF, JSON]
    body = b'{"password": "correct horse", "code": "K7QM-3XRP-W9TD"}'
    r = await http_call(app, "POST", "/api/auth/setup", headers, body)
    assert r.status == 403
    assert r.json()["error"]["code"] == "origin_not_allowed"
    status = await http_call(app, "GET", "/api/auth/status", [OK_HOST])
    assert status.json() == {"setup": False, "authenticated": False, "setup_code": "required"}


async def test_real_app_form_post_is_refused(http_call: Any, app: FastAPI, started: Any) -> None:
    # A classic CSRF form: same Origin cannot be faked by a page, but even with it the missing
    # header and the form encoding are refused.
    headers = [OK_HOST, OK_ORIGIN, ("content-type", "application/x-www-form-urlencoded")]
    r = await http_call(app, "POST", "/api/auth/setup", headers, b"password=correct+horse")
    assert r.status == 403
    assert r.json()["error"]["code"] == "missing_csrf_header"
