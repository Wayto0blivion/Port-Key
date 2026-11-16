"""Entry point: ``python -m portkey`` runs uvicorn with the settings from ``config.py``."""

from __future__ import annotations

import uvicorn

from portkey.config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(
        "portkey.app:create_app",
        factory=True,
        host=config.listen_host,
        port=config.listen_port,
        log_level=config.log_level,
        server_header=False,
        proxy_headers=False,
        ws_ping_interval=config.limits.ws_ping_interval,
        ws_ping_timeout=config.limits.ws_ping_timeout,
        ws_max_size=config.limits.ws_max_frame_bytes,
    )


if __name__ == "__main__":
    main()
