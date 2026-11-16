"""End-to-end harness (package 7); the specs (``tests/e2e/test_*.py``) are package 9's.

Run with ``./mk e2e P=<project>``: it builds and starts the TEST-ONLY sshd containers, the
``app`` service (the real runtime image with the PC's Host/Origin allowlist, listening on 8822,
the fixture .ssh directory copied to a 0777 tmpfs at /ssh, data in a tmpfs) and the
``evil-origin`` page, then
runs ``pytest tests/e2e -m e2e`` in the Playwright container. Every test under ``tests/e2e`` is
marked ``e2e`` automatically. State lives for the whole run: once a test sets the app password
(``app_token``), the first-run screen is gone, so a first-run spec must not use ``app_token``
and must run first (name its file so it sorts first, e.g. ``test_00_first_run.py``).

Fixtures

- ``base_url`` (``http://localhost:8822``, the PC's URL: Chromium maps ``localhost`` to the
  ``app`` container, see ``browser_args``, and ``AppApi`` connects to ``$PORTKEY_CONNECT_TO``),
  ``evil_url`` (``http://evil-origin:8080``).
- ``app_api``: ``AppApi`` (httpx with allowed Host, Origin and ``X-Port-Key``), app healthy.
- ``app_token``: a bearer token; sets ``testbed.APP_PASSWORD`` on first use.
- ``browser`` (Chromium, headless, session), ``context`` (per test, ``base_url`` set,
  clipboard permissions granted), ``page``; a failing test leaves a screenshot in
  ``test-results/``.
- ``signed_in_page``: ``page`` already holding ``app_token`` and showing the app.
- ``evil_origin``: ``evil_origin(ws_url=..., token=...)`` runs the attack page in ``page``.
- ``sshd_servers`` / ``ssh_users`` / ``totp``: the test bed as the app sees it (``sshd:22``,
  ``sshd:2222``, ``sshd-rekeyed:22``; ``sshd-inner`` only through ``sshd``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from tests.e2e.harness.app_api import AppApi
from tests.e2e.harness.browser import run_evil_origin, sign_in_with_token
from tests.fixtures import testbed as tb

_HERE = Path(__file__).resolve().parent
RESULTS_DIR = _HERE.parent.parent / "test-results"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.e2e)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    setattr(item, f"rep_{report.when}", report)


# --- the services --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("PORTKEY_BASE_URL", "http://localhost:8822").rstrip("/")


@pytest.fixture(scope="session")
def evil_url() -> str:
    return os.environ.get("PORTKEY_EVIL_URL", "http://evil-origin:8080").rstrip("/")


@pytest.fixture(scope="session")
def app_api(base_url: str) -> Iterator[AppApi]:
    api = AppApi(base_url)
    try:
        api.wait_healthy()
    except RuntimeError as exc:
        pytest.fail(f"{exc}. Run e2e tests with ./mk e2e P=<project>.", pytrace=False)
    yield api
    api.close()


@pytest.fixture(scope="session")
def app_token(app_api: AppApi) -> str:
    return app_api.ensure_password()


@pytest.fixture(scope="session")
def sshd_servers() -> dict[str, tb.SshServer]:
    return {
        "sshd": tb.SSHD,
        "sshd-2222": tb.SSHD_ALT_PORT,
        "sshd-rekeyed": tb.SSHD_REKEYED,
        "sshd-inner": tb.SSHD_INNER,
    }


@pytest.fixture(scope="session")
def ssh_users() -> dict[str, tb.SshUser]:
    return dict(tb.USERS)


@pytest.fixture(scope="session")
def totp() -> tb.Totp:
    return tb.Totp()


# --- the browser -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def playwright_driver() -> Iterator[Playwright]:
    with sync_playwright() as driver:
        yield driver


def browser_args(base_url: str) -> list[str]:
    """Chromium flags that send ``base_url``'s host to ``$PORTKEY_CONNECT_TO``'s host.

    The browser then opens http://localhost:8822 as the owner's browser does: a secure context
    (async clipboard, Cross-Origin-Opener-Policy honoured), the PC's Host and Origin, and a
    same-origin ws://localhost:8822 under the CSP's connect-src 'self'."""
    connect_to = os.environ.get("PORTKEY_CONNECT_TO", "")
    shown = urlsplit(base_url).hostname or ""
    target = connect_to.rsplit(":", 1)[0]
    if not target or target == shown:
        return []
    return [f"--host-resolver-rules=MAP {shown} {target}"]


@pytest.fixture(scope="session")
def browser(playwright_driver: Playwright, base_url: str) -> Iterator[Browser]:
    instance = playwright_driver.chromium.launch(headless=True, args=browser_args(base_url))
    yield instance
    instance.close()


@pytest.fixture
def context(browser: Browser, base_url: str) -> Iterator[BrowserContext]:
    ctx = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 800})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    ctx.set_default_timeout(10_000)
    yield ctx
    ctx.close()


@pytest.fixture
def page(
    context: BrowserContext, request: pytest.FixtureRequest, app_api: AppApi
) -> Iterator[Page]:
    tab = context.new_page()
    yield tab
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        RESULTS_DIR.mkdir(exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-150:]
        try:
            tab.screenshot(path=str(RESULTS_DIR / f"{name}.png"), full_page=True)
        except Exception as exc:  # a screenshot must never hide the real failure
            print(f"screenshot failed: {type(exc).__name__}")


@pytest.fixture
def signed_in_page(page: Page, base_url: str, app_token: str) -> Page:
    sign_in_with_token(page, base_url, app_token)
    return page


@pytest.fixture
def evil_origin(page: Page, evil_url: str) -> Callable[..., dict[str, Any]]:
    def run(**kwargs: Any) -> dict[str, Any]:
        return run_evil_origin(page, evil_url, **kwargs)

    return run
