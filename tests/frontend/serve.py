"""Static file server for the frontend shell tests (package 8a, test-only).

Serves ``portkey/static`` with the app's own Content-Security-Policy
(``portkey.security.headers.CSP``), so an inline script in the frontend fails here the same way
it would in the app, and xterm's injected styles pass as they do there. The API is not served:
tests inject ``mock/server.js``.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from portkey.security.headers import CSP

STATIC = Path(__file__).resolve().parents[2] / "portkey" / "static"


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def start(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the server in a daemon thread; returns it and its base URL."""
    handler = functools.partial(_Handler, directory=str(STATIC))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}"
