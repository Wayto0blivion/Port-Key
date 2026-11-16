"""Security response headers and the CSP, checked against the vendored xterm."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from portkey.security.headers import CSP, SecurityHeadersMiddleware

XTERM = Path(__file__).resolve().parents[3] / "portkey" / "static" / "vendor" / "xterm"


def _directives() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in CSP.split(";"):
        name, *values = part.split()
        out[name] = values
    return out


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/nope", "/js/main.js"])
async def test_every_response_has_security_headers(client: httpx.AsyncClient, path: str) -> None:
    r = await client.get(path)
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"


async def test_api_responses_are_not_cached(client: httpx.AsyncClient) -> None:
    for path in ("/api/health", "/api/auth/status", "/api/nope"):
        assert (await client.get(path)).headers["cache-control"] == "no-store"


async def test_error_responses_have_headers_too(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"password": 1})
    assert r.status_code == 422
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["cache-control"] == "no-store"


def test_csp_is_tight() -> None:
    d = _directives()
    assert d["default-src"] == ["'none'"]
    assert d["script-src"] == ["'self'"]
    assert d["connect-src"] == ["'self'"]
    assert d["img-src"] == ["'self'"]
    assert d["frame-ancestors"] == ["'none'"]
    assert d["base-uri"] == ["'none'"]
    assert d["form-action"] == ["'none'"]
    assert "'unsafe-eval'" not in CSP
    assert "data:" not in CSP
    assert "*" not in CSP
    # The only relaxation, justified by the next test.
    assert d["style-src"] == ["'self'", "'unsafe-inline'"]


def test_vendored_xterm_needs_inline_styles() -> None:
    """If this fails, xterm no longer injects inline styles: drop 'unsafe-inline' from CSP."""
    source = (XTERM / "xterm.js").read_text(encoding="utf-8")
    assert 'createElement("style")' in source
    assert 'setAttribute("style"' in source


def test_vendored_scripts_need_no_eval() -> None:
    for name in ("xterm.js", "addon-fit.js", "addon-search.js"):
        source = (XTERM / name).read_text(encoding="utf-8")
        assert "eval(" not in source, name
        assert "new Function(" not in source, name
        assert "new Worker(" not in source, name


def test_index_html_has_no_inline_script_or_style() -> None:
    html = (XTERM.parents[1] / "index.html").read_text(encoding="utf-8")
    assert "<style" not in html
    assert " style=" not in html
    for tag in html.split("<script")[1:]:
        assert "src=" in tag.split(">", 1)[0], "inline <script> would be blocked by the CSP"


async def test_websocket_scope_passes_through_untouched() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])
        await send({"type": "websocket.accept"})

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    await SecurityHeadersMiddleware(inner)(
        {"type": "websocket", "path": "/ws/terminal"}, None, send
    )  # type: ignore[arg-type]
    assert seen == ["websocket"]
    assert sent == [{"type": "websocket.accept"}]


async def test_existing_header_is_overridden() -> None:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": ((b"content-security-policy", b"default-src *"),),
            }
        )
        await send({"type": "http.response.body", "body": b""})

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    await SecurityHeadersMiddleware(inner)(
        {"type": "http", "path": "/x", "headers": []}, None, send
    )  # type: ignore[arg-type]
    csp = [v for k, v in sent[0]["headers"] if k == b"content-security-policy"]
    assert csp == [CSP.encode()]
    assert all(k != b"cache-control" for k, _ in sent[0]["headers"])
