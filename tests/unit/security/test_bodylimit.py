"""Request body cap (security review): a local process without a token must not be able to make
the app hold an unbounded body in memory before auth runs."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from starlette.types import Message, Receive, Scope, Send

from portkey.config import Limits
from portkey.security.bodylimit import BodyLimitMiddleware
from tests.unit.security.conftest import HeaderList, _scope

CAP = 1000
MUTATING: HeaderList = [
    ("host", "localhost:8822"),
    ("origin", "http://localhost:8822"),
    ("x-port-key", "1"),
    ("content-type", "application/json"),
]


class Feed:
    """An ASGI ``receive`` that hands out ``chunks`` and counts how many were taken."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.taken = 0

    async def __call__(self) -> Message:
        if self.taken < len(self.chunks):
            body = self.chunks[self.taken]
            self.taken += 1
            return {
                "type": "http.request",
                "body": body,
                "more_body": self.taken < len(self.chunks),
            }
        return {"type": "http.disconnect"}


async def drive(
    app: object, headers: HeaderList, feed: Feed, path: str = "/api/connections"
) -> tuple[int, bytes, list[Message]]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    asgi = app  # an ASGI callable
    await asgi(_scope("http", path, headers, "POST"), feed, send)  # type: ignore[operator]
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, sent
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return starts[0]["status"], body, sent


class Echo:
    """Downstream app that reads the whole body, then answers 200 with its length."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        size = 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(size).encode()})


def test_the_default_cap_is_far_above_any_real_body_and_far_below_harm() -> None:
    # 1000 ids in a bulk edit is about 40 KB of JSON.
    assert 64 * 1024 <= Limits().max_body_bytes <= 1024 * 1024


async def test_declared_length_over_the_cap_is_refused_unread() -> None:
    echo = Echo()
    feed = Feed([b"x" * 10])
    status, body, _ = await drive(
        BodyLimitMiddleware(echo, CAP), [*MUTATING, ("content-length", str(CAP + 1))], feed
    )
    assert status == 413
    assert b'"payload_too_large"' in body
    assert echo.calls == 0
    assert feed.taken == 0


@pytest.mark.parametrize("value", ["abc", "-5", "1e9", ""])
async def test_a_malformed_length_is_refused(value: str) -> None:
    echo = Echo()
    status, _, _ = await drive(
        BodyLimitMiddleware(echo, CAP), [*MUTATING, ("content-length", value)], Feed([b"{}"])
    )
    assert status == 413
    assert echo.calls == 0


async def test_a_repeated_length_is_refused() -> None:
    echo = Echo()
    headers = [*MUTATING, ("content-length", "2"), ("content-length", "2")]
    status, _, _ = await drive(BodyLimitMiddleware(echo, CAP), headers, Feed([b"{}"]))
    assert status == 413


async def test_a_body_at_the_cap_passes() -> None:
    echo = Echo()
    feed = Feed([b"x" * 600, b"x" * 400])
    status, body, _ = await drive(BodyLimitMiddleware(echo, CAP), MUTATING, feed)
    assert (status, body) == (200, b"1000")


async def test_a_chunked_body_stops_being_read_past_the_cap() -> None:
    echo = Echo()
    feed = Feed([b"x" * 400] * 1000)  # 400 KB with no Content-Length
    status, body, sent = await drive(BodyLimitMiddleware(echo, CAP), MUTATING, feed)
    assert status == 413
    assert b'"payload_too_large"' in body
    assert feed.taken == 3  # 1200 bytes: the third chunk passed the cap, nothing after it
    assert all(m.get("status") != 200 for m in sent)


async def test_the_refusal_carries_the_security_headers() -> None:
    _, _, sent = await drive(
        BodyLimitMiddleware(Echo(), CAP), [*MUTATING, ("content-length", "5000")], Feed([])
    )
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert b"content-security-policy" in headers


# --- the real app: no token, a huge body ---------------------------------------------------------


async def test_the_app_refuses_a_huge_body_without_reading_it(app: FastAPI) -> None:
    feed = Feed([b"{" + b" " * 65536] * 10_000)
    headers = [*MUTATING, ("content-length", str(65537 * 10_000))]
    status, _, _ = await drive(app, headers, feed)
    assert status == 413
    assert feed.taken == 0


async def test_the_app_stops_reading_a_huge_chunked_body_before_auth(app: FastAPI) -> None:
    # No Authorization header: before the cap, FastAPI read the whole body, then said 401.
    feed = Feed([b" " * 65536] * 10_000)  # 655 MB if it were all read
    status, _, _ = await drive(app, MUTATING, feed)
    assert status == 413
    assert feed.taken * 65536 <= Limits().max_body_bytes + 65536


async def test_the_app_still_reads_ordinary_bodies(app: FastAPI) -> None:
    # Reaches auth (401), so the cap let the body through.
    status, _, _ = await drive(app, MUTATING, Feed([b"{}"]))
    assert status == 401


def test_middleware_order_puts_the_cap_inside_the_host_guard(app: FastAPI) -> None:
    names = [m.cls.__name__ for m in app.user_middleware]  # type: ignore[attr-defined]
    assert names.index("HostGuardMiddleware") < names.index("BodyLimitMiddleware")


async def test_a_request_reaches_the_route_with_its_body(app: FastAPI) -> None:
    seen: list[bytes] = []
    probe = FastAPI()

    @probe.post("/api/probe")
    async def probe_route(request: Request) -> dict[str, int]:
        data = await request.body()
        seen.append(data)
        return {"n": len(data)}

    status, _, _ = await drive(
        BodyLimitMiddleware(probe, CAP), MUTATING, Feed([b'{"a":', b"1}"]), path="/api/probe"
    )
    assert status == 200
    assert seen == [b'{"a":1}']
