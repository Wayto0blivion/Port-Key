"""Container healthcheck: ``python -m portkey.healthcheck`` exits 0 when /api/health answers.

It sends an allowed Host header, because the host guard rejects ``127.0.0.1:8000``.
"""

from __future__ import annotations

import http.client
import sys

from portkey.config import load_config


def main() -> int:
    config = load_config()
    conn = http.client.HTTPConnection("127.0.0.1", config.listen_port, timeout=4)
    try:
        conn.request("GET", "/api/health", headers={"Host": config.allowed_hosts[0]})
        return 0 if conn.getresponse().status == 200 else 1
    except OSError:
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
