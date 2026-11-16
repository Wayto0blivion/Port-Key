"""HTTP access to the ``app`` service for the e2e harness (package 7).

Specs drive the UI with Playwright; this client is for setup and checks around it: waiting for
the app, setting the app password, reading state. It sends what the browser would: an allowed
Host (from the base URL), the matching Origin, ``X-Port-Key: 1`` and a JSON body on every
non-GET request.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from tests.fixtures import testbed as tb


class AppApi:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.host = urlsplit(self.base_url).netloc
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Origin": self.base_url, "X-Port-Key": "1"},
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    # --- plumbing ----------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """A request as the browser would send it. Non-GET requests get ``{}`` when ``json`` is
        None. ``headers`` override the defaults (``{"Origin": "http://evil-origin:8080"}``)."""
        merged = dict(headers or {})
        if token:
            merged.setdefault("Authorization", f"Bearer {token}")
        if method.upper() not in ("GET", "HEAD", "OPTIONS") and json is None:
            json = {}
        return self.client.request(method, path, json=json, headers=merged)

    def raw(self, method: str, path: str, *, host: str, **kwargs: Any) -> httpx.Response:
        """Send with an explicit Host header (DNS-rebinding style checks)."""
        headers = {"Host": host, **kwargs.pop("headers", {})}
        return self.client.request(method, path, headers=headers, **kwargs)

    def wait_healthy(self, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last = "no answer"
        while time.monotonic() < deadline:
            try:
                r = self.client.get("/api/health")
                if r.status_code == 200:
                    body: dict[str, Any] = r.json()
                    return body
                last = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            time.sleep(0.5)
        raise RuntimeError(f"{self.base_url}/api/health did not answer 200 ({last})")

    # --- auth --------------------------------------------------------------------------------

    def auth_status(self, token: str | None = None) -> dict[str, Any]:
        r = self.request("GET", "/api/auth/status", token=token)
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        return body

    def setup(self, password: str = tb.APP_PASSWORD) -> str:
        r = self.request("POST", "/api/auth/setup", json={"password": password})
        r.raise_for_status()
        return str(r.json()["token"])

    def login(self, password: str = tb.APP_PASSWORD) -> str:
        r = self.request("POST", "/api/auth/login", json={"password": password})
        r.raise_for_status()
        return str(r.json()["token"])

    def ensure_password(self, password: str = tb.APP_PASSWORD) -> str:
        """A valid token: sets the app password on first use, otherwise signs in.

        The app's database lives in a tmpfs for the whole e2e run, so once any test calls this,
        the first-run screen is gone until the next ``./mk e2e``.
        """
        if self.auth_status()["setup"]:
            return self.login(password)
        return self.setup(password)
