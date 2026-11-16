"""Entry point: ``python -m portkey`` runs uvicorn with the settings from ``config.py``."""

from __future__ import annotations

import copy
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from portkey.config import Config, load_config
from portkey.security.redact import SETUP_CODE_LOGGER


def log_config() -> dict[str, Any]:
    """uvicorn's own logging, plus the first-visit setup code's announcement on the same
    stderr handler. It is always at INFO, whatever ``PORTKEY_LOG_LEVEL`` says, so the owner
    finds the code in ``docker compose logs portkey``."""
    config: dict[str, Any] = copy.deepcopy(LOGGING_CONFIG)
    config["loggers"][SETUP_CODE_LOGGER] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def server_options(config: Config) -> dict[str, Any]:
    """Everything ``uvicorn.run`` gets besides the app."""
    return {
        "factory": True,
        "host": config.listen_host,
        "port": config.listen_port,
        "log_level": config.log_level,
        "log_config": log_config(),
        "server_header": False,
        "proxy_headers": False,
        "ws_ping_interval": config.limits.ws_ping_interval,
        "ws_ping_timeout": config.limits.ws_ping_timeout,
        "ws_max_size": config.limits.ws_max_frame_bytes,
        # On `docker compose stop`, open sockets get this long to finish before uvicorn cancels
        # them; then the lifespan shutdown closes every session. Docker kills after 10 s.
        "timeout_graceful_shutdown": config.limits.graceful_shutdown_seconds,
    }


def main() -> None:
    uvicorn.run("portkey.app:create_app", **server_options(load_config()))


if __name__ == "__main__":
    main()
