"""The setup code in a running app: one framed startup record, printed on every start until a
password is set, and nowhere else (no response, event record, database file or other log
line). Generation, comparison and backoff are in test_setup_code.py; the reset path is in
test_reset_password.py."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from uvicorn.logging import DefaultFormatter

from portkey.__main__ import log_config
from portkey.config import Config
from portkey.security import auth as auth_module
from portkey.security import setup_code as sc
from portkey.security.redact import SETUP_CODE_LOGGER, RedactingFilter
from tests.unit.security.setup_log import codes_in_log, running

PW = "correct horse battery"
REPO = Path(__file__).resolve().parents[3]


def forms(code: str) -> list[str]:
    """Every way the code could show up: as printed, compact, lower-case."""
    compact = code.replace("-", "")
    return [code, compact, code.lower(), compact.lower()]


def announcements(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.name == SETUP_CODE_LOGGER]


async def test_startup_prints_one_framed_record(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    async with running(config) as (_, client):
        [record] = announcements(caplog.records)
        [code] = codes_in_log(caplog.records)
        assert record.levelno == logging.INFO
        # The redaction filter (on uvicorn's handler too) leaves this one record intact.
        assert RedactingFilter().filter(record) is True
        text = record.getMessage()
        assert f"Setup code:   {code}" in text
        assert "http://localhost:8822" in text
        assert '"Setup code" field' in text
        rule = "=" * 72
        assert text.strip().startswith(rule)
        assert text.strip().endswith(rule)
        assert (await client.get("/api/auth/status")).json()["setup_code"] == "required"


async def test_uvicorn_prints_the_record_at_info_whatever_the_log_level(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = log_config()
    assert cfg["loggers"][SETUP_CODE_LOGGER] == {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    assert cfg["handlers"]["default"]["stream"] == "ext://sys.stderr"
    async with running(config):
        [record] = announcements(caplog.records)
        [code] = codes_in_log(caplog.records)
    formatter = DefaultFormatter(cfg["formatters"]["default"]["fmt"], use_colors=False)
    printed = formatter.format(record)
    assert printed.startswith("INFO:")
    assert sc.find_in_log(printed) == code


async def test_every_start_prints_a_new_code_until_a_password_is_set(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    async with running(config):
        pass
    async with running(config) as (_, client):
        first, second = codes_in_log(caplog.records)
        assert first != second
        r = await client.post("/api/auth/setup", json={"password": PW, "code": first})
        assert (r.status_code, r.json()["error"]["code"]) == (403, "bad_setup_code")
        r = await client.post("/api/auth/setup", json={"password": PW, "code": second})
        assert r.status_code == 200
    caplog.clear()
    async with running(config) as (_, client):
        assert announcements(caplog.records) == []
        assert "Setup code" not in caplog.text
        status = (await client.get("/api/auth/status")).json()
        assert status == {"setup": True, "authenticated": False, "setup_code": None}


async def test_the_code_appears_nowhere_but_the_startup_record(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    responses: list[httpx.Response] = []
    async with running(config) as (services, client):
        [code] = codes_in_log(caplog.records)
        variants = forms(code)

        async def call(method: str, path: str, **kw: Any) -> httpx.Response:
            r = await client.request(method, path, **kw)
            responses.append(r)
            return r

        await call("GET", "/api/auth/status")
        await call("POST", "/api/auth/login", json={"password": PW})
        for typed in ("AAAA-BBBB-CCCC", code[:-1]):
            r = await call("POST", "/api/auth/setup", json={"password": PW, "code": typed})
            assert r.status_code == 403
        for body in (
            {"password": "short", "code": code},
            {"password": PW, "code": code, "extra": code},
            {"password": PW, "code": [code]},
            {"password": PW},
        ):
            assert (await call("POST", "/api/auth/setup", json=body)).status_code == 422
        # Compact and lower-case, as the owner might type it.
        r = await call("POST", "/api/auth/setup", json={"password": PW, "code": variants[3]})
        assert r.status_code == 200
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        r = await call("POST", "/api/auth/setup", json={"password": PW, "code": code})
        assert r.status_code == 409
        await call("POST", "/api/auth/login", json={"password": PW})
        for path in (
            "/api/auth/status",
            "/api/connections",
            "/api/groups",
            "/api/tags",
            "/api/keys",
            "/api/hosts/suggestions",
            "/api/hostkeys",
            "/api/sessions",
            "/api/settings",
            "/api/diag",
            "/api/openapi.json",
            "/api/no-such-thing",
            "/",
        ):
            assert (await call("GET", path, headers=auth)).status_code < 500
        events = [repr(e) for e in services.events.recent(5000)]
        dump = "\n".join(services.db.conn.iterdump())

    for r in responses:
        seen = r.text + repr(r.headers)
        assert not [v for v in variants if v in seen], (r.request.url, r.status_code)
    for v in variants:
        assert v not in "\n".join(events)
        assert v not in dump
    for file in config.data_dir.iterdir():  # memory only: not in the database or its WAL
        raw = file.read_bytes()
        assert not [v for v in variants if v.encode() in raw], file.name
    carrying = [r for r in caplog.records if any(v in r.getMessage() for v in variants)]
    assert [r.name for r in carrying] == [SETUP_CODE_LOGGER]


async def test_other_log_lines_scrub_the_code_as_a_backstop(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    async with running(config):
        [code] = codes_in_log(caplog.records)
        caplog.clear()
        logging.getLogger("portkey.test").warning("echo %s", code)
        logging.getLogger("uvicorn.error").warning("echo %s", code.lower().replace("-", " "))
        logging.getLogger("portkey.test").warning("echo " + code.replace("-", ""))
    assert len(caplog.records) == 3
    for record in caplog.records:
        assert "[redacted]" in record.getMessage()
        assert not [v for v in forms(code) if v in record.getMessage()]


@pytest.mark.parametrize("file", ["compose.yaml", "compose.build.yaml"])
def test_the_compose_service_name_matches_messages_and_the_form(file: str) -> None:
    compose = (REPO / file).read_text()
    services = compose.split("\nservices:\n", 1)[1].split("\n\n", 1)[0]
    names = re.findall(r"^  ([A-Za-z0-9_.-]+):$", services, re.MULTILINE)
    assert names == [sc.COMPOSE_SERVICE]
    command = f"docker compose logs {sc.COMPOSE_SERVICE}"
    assert command in auth_module.WRONG_CODE_MESSAGE
    assert command in (REPO / "portkey/static/js/views/login.js").read_text()
    assert command in (REPO / "README.md").read_text()
