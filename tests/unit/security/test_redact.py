"""Log redaction backstop and the 422 handler that never echoes request input."""

from __future__ import annotations

import logging
import sys
from typing import Any

import httpx
import pytest
from fastapi.exceptions import RequestValidationError

from portkey.security.redact import (
    RedactingFilter,
    install_log_redaction,
    redact,
    validation_exception_handler,
)

TOKEN = "q3JxV9mT2aLk8PzR4wYb6NcE1uHs0dFg5iOj7XvQ2Ze"
KEY_BLOCK = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


# --- redact() ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"Authorization: Bearer {TOKEN}", TOKEN),
        (f"headers={{'authorization': 'Bearer {TOKEN}'}}", TOKEN),
        ("password=hunter2-long", "hunter2-long"),
        ("passphrase: 'open sesame'", "open sesame"),
        ('{"password": "correct horse"}', "correct horse"),
        ("{'old': 'old pass', 'new': 'new pass'}", "old pass"),
        ("{'old': 'old pass', 'new': 'new pass'}", "new pass"),
        ('{"type": "prompt_reply", "id": "p5", "value": "s3cret-pw"}', "s3cret-pw"),
        ('{"type": "prompt_reply", "id": "p6", "values": ["492039"]}', "492039"),
        (f"token={TOKEN}&x=1", TOKEN),
        (f"loaded key {KEY_BLOCK} ok", "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA truncated", "MIIEpAIBAAKCAQEA"),
    ],
)
def test_redact_removes_secrets(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert "[redacted" in out


@pytest.mark.parametrize(
    "text",
    [
        "connected to web-prod 192.168.10.42:22 as john",
        "host key SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E accepted",
        "auth failed: password_not_allowed",
        "prompt kind password for hop 1",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI john@WORKPC",
    ],
)
def test_redact_leaves_allowed_detail_alone(text: str) -> None:
    assert redact(text) == text


# --- the logging filter --------------------------------------------------------------------------


def _record(msg: str, *args: Any, exc_info: Any = None) -> logging.LogRecord:
    return logging.LogRecord("portkey.test", logging.INFO, __file__, 1, msg, args, exc_info)


def test_filter_redacts_message_args() -> None:
    record = _record("login with password=%s", "hunter2-long")
    assert RedactingFilter().filter(record) is True
    assert "hunter2-long" not in record.getMessage()


def test_filter_keeps_args_shape_when_args_carry_the_secret() -> None:
    record = _record('%s "%s %s" %d', "172.17.0.1", "GET", f"/api/x?token={TOKEN}", 200)
    RedactingFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert len(record.args) == 4
    assert TOKEN not in record.getMessage()


def test_filter_leaves_clean_records_untouched() -> None:
    record = _record("opened %s", "web-prod")
    RedactingFilter().filter(record)
    assert record.msg == "opened %s"
    assert record.args == ("web-prod",)


def test_filter_redacts_exception_text() -> None:
    try:
        raise ValueError(f"bad header Bearer {TOKEN}")
    except ValueError:
        record = _record("failed", exc_info=sys.exc_info())
    RedactingFilter().filter(record)
    assert record.exc_text is not None
    assert TOKEN not in record.exc_text
    assert "ValueError" in record.exc_text


def test_filter_survives_broken_records() -> None:
    record = _record("%s %s", "only one")
    assert RedactingFilter().filter(record) is True


@pytest.fixture
def restore_logging() -> Any:
    factory = logging.getLogRecordFactory()
    asyncssh_level = logging.getLogger("asyncssh").level
    yield
    logging.setLogRecordFactory(factory)
    logging.getLogger("asyncssh").setLevel(asyncssh_level)


def test_install_caps_asyncssh_at_warning(restore_logging: Any) -> None:
    logging.getLogger("asyncssh").setLevel(logging.DEBUG)
    install_log_redaction()
    assert logging.getLogger("asyncssh").level == logging.WARNING
    assert not logging.getLogger("asyncssh.connection").isEnabledFor(logging.INFO)


def test_install_is_idempotent(restore_logging: Any) -> None:
    install_log_redaction()
    factory = logging.getLogRecordFactory()
    install_log_redaction()
    assert logging.getLogRecordFactory() is factory
    root_filters = [f for f in logging.getLogger().filters if isinstance(f, RedactingFilter)]
    assert len(root_filters) == 1


def test_child_logger_records_are_redacted(
    restore_logging: Any, caplog: pytest.LogCaptureFixture
) -> None:
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("portkey.ssh.client").info("auth with passphrase=%s", "open-sesame")
        logging.getLogger("uvicorn.error").warning("got Bearer %s", TOKEN)
    assert "open-sesame" not in caplog.text
    assert TOKEN not in caplog.text
    assert "[redacted]" in caplog.text


# --- 422 handler ---------------------------------------------------------------------------------


async def test_422_never_echoes_input(client: httpx.AsyncClient) -> None:
    secret = "my-secret-password-value"
    r = await client.post("/api/auth/login", json={"password": secret, "extra": secret})
    assert r.status_code == 422
    assert secret not in r.text
    body = r.json()["error"]
    assert body["code"] == "validation"
    assert body["message"] == "The request is not valid."
    for field in body["fields"]:
        assert set(field) == {"loc", "msg", "type"}
    assert ["body", "extra"] in [f["loc"] for f in body["fields"]]


async def test_422_for_too_long_password_hides_it(client: httpx.AsyncClient) -> None:
    secret = "Z" * 1100
    r = await client.post("/api/auth/setup", json={"password": secret, "code": "K7QM-3XRP-W9TD"})
    assert r.status_code == 422
    assert "ZZZZ" not in r.text
    assert r.json()["error"]["fields"][0]["type"] in {"too_long", "string_too_long"}


async def test_422_for_invalid_json(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login",
        content=b'{"password": "half-typed-secret',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert "half-typed-secret" not in r.text
    assert r.json()["error"]["fields"][0]["type"] == "json_invalid"


async def test_422_for_wrong_type(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"password": ["a-list-secret"]})
    assert r.status_code == 422
    assert "a-list-secret" not in r.text


async def test_handler_scrubs_messages_that_quote_the_input() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "host"),
                "msg": "Value error, web-prod@evil is not a host",
                "input": "web-prod@evil",
                "ctx": {"error": "web-prod@evil"},
                "url": "https://errors.pydantic.dev",
            }
        ]
    )
    response = await validation_exception_handler(None, exc)  # type: ignore[arg-type]
    text = bytes(response.body).decode()
    assert "web-prod@evil" not in text
    assert "ctx" not in text
    assert "pydantic.dev" not in text
    assert response.status_code == 422
