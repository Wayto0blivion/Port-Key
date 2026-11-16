"""``python -m portkey``: the options uvicorn runs with."""

from __future__ import annotations

import inspect

import uvicorn

from portkey.__main__ import server_options
from portkey.config import Config


def test_uvicorn_accepts_every_option() -> None:
    # Checked against the signature: building a uvicorn.Config would apply log_config to the
    # whole test process (dictConfig) and break later tests' log capture.
    accepted = set(inspect.signature(uvicorn.Config).parameters)
    options = server_options(Config())
    assert set(options) <= accepted
    assert (options["ws_max_size"], options["server_header"]) == (64 * 1024, False)


def test_shutdown_does_not_wait_forever_for_open_sockets() -> None:
    # Review finding: a socket stuck in a write never saw the shutdown close, so uvicorn never
    # reached the lifespan shutdown (sessions closed, database closed) before Docker's SIGKILL.
    options = server_options(Config())
    assert 0 < options["timeout_graceful_shutdown"] < 10  # Docker stops with SIGKILL at 10 s
