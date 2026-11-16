"""Integration fixtures (package 7): the sshd test bed from compose.dev.yaml.

Run with ``./mk integration P=<project>``: it starts ``sshd`` (host key set A, ports 22 and
2222, on both networks), ``sshd-rekeyed`` (set B) and ``sshd-inner`` (set A, internal network
only), then runs ``pytest tests/integration -m integration`` in the ``integration`` service.
Every test collected under ``tests/integration`` is marked ``integration`` automatically.

Fixtures

- ``sshd``, ``sshd_alt_port``, ``sshd_rekeyed``, ``sshd_inner``: ``testbed.SshServer`` (host,
  port, host key set). ``sshd_inner`` is reachable only with ``tunnel=`` through ``sshd``.
- ``ssh_users``: ``{name: SshUser}`` with passwords and accepted methods (pw, keyonly, fresh,
  tfa, fishy, badperms, nosh).
- ``hostkey_fingerprints``: ``{"a": "SHA256:...", "b": "SHA256:..."}``; ``hostkey_publics``
  the same as ``"ssh-ed25519 AAAA..."``.
- ``totp``: ``testbed.Totp`` for the ``tfa`` user (``now()``, ``fresh()``, ``afresh()``).
- ``client_keys``: ``{name: ClientKey}`` for the TEST-ONLY keys in the fixture .ssh directory
  (``.file``, ``.passphrase``, ``.encrypted``); ``key_passphrase``.
- ``fake_prompter``: a ``FakePrompter`` factory: ``fake_prompter(hostkey_new=True, ...)``.
- ``fixture_ssh_dir``: a private copy of the fixture .ssh directory with every mode 0777, as a
  Windows bind mount looks; ``portkey_config``: a ``Config`` using it, with empty temp data and
  generated-keys dirs and the Windows display paths.
- ``ssh_connect``: ``await ssh_connect(server, user, password=..., key=..., totp=...,
  tunnel=...)`` opens an asyncssh connection that pins the server's expected host key.
- ``reset_ssh_user``: ``await reset_ssh_user("fresh")`` restores a user's ``~/.ssh`` to the
  image's initial state (after a wizard test installed a key). Not for ``nosh``.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from portkey.config import Config
from tests.fixtures import sshclient
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import FakePrompter
from tests.fixtures.sshclient import SshConnect

_HERE = Path(__file__).resolve().parent
SSHD_WAIT_SECONDS = 60.0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.integration)


# --- reachability --------------------------------------------------------------------------


def _wait_tcp(host: str, port: int, deadline: float) -> str | None:
    """None once host:port accepts a TCP connection; the last error at the deadline."""
    error = "not tried"
    unknown_name_deadline = time.monotonic() + 10.0
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                return None
        except socket.gaierror as exc:
            # The name stays unknown when the container is not on this network at all.
            error = f"name not resolved: {exc}"
            if time.monotonic() >= min(deadline, unknown_name_deadline):
                return error
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            return error
        time.sleep(0.5)


@pytest.fixture(scope="session", autouse=True)
def _sshd_up() -> None:
    """Fail fast (not hang) when the sshd containers are not there."""
    deadline = time.monotonic() + SSHD_WAIT_SECONDS
    for server in (tb.SSHD, tb.SSHD_ALT_PORT, tb.SSHD_REKEYED):
        error = _wait_tcp(server.host, server.port, deadline)
        if error:
            pytest.fail(
                f"{server.host}:{server.port} is not reachable ({error}). Run integration tests "
                "with ./mk integration P=<project>, which starts the sshd containers.",
                pytrace=False,
            )


# --- the test bed ----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sshd() -> tb.SshServer:
    return tb.SSHD


@pytest.fixture(scope="session")
def sshd_alt_port() -> tb.SshServer:
    return tb.SSHD_ALT_PORT


@pytest.fixture(scope="session")
def sshd_rekeyed() -> tb.SshServer:
    return tb.SSHD_REKEYED


@pytest.fixture(scope="session")
def sshd_inner() -> tb.SshServer:
    return tb.SSHD_INNER


@pytest.fixture(scope="session")
def ssh_users() -> dict[str, tb.SshUser]:
    return dict(tb.USERS)


@pytest.fixture(scope="session")
def hostkey_publics() -> dict[str, str]:
    return {s: tb.hostkey_public(s) for s in ("a", "b")}


@pytest.fixture(scope="session")
def hostkey_fingerprints() -> dict[str, str]:
    return {s: tb.hostkey_fingerprint(s) for s in ("a", "b")}


@pytest.fixture(scope="session")
def totp() -> tb.Totp:
    return tb.Totp()


@pytest.fixture(scope="session")
def client_keys() -> dict[str, tb.ClientKey]:
    return dict(tb.CLIENT_KEYS)


@pytest.fixture(scope="session")
def key_passphrase() -> str:
    return tb.KEY_PASSPHRASE


@pytest.fixture
def fake_prompter() -> Callable[..., FakePrompter]:
    return FakePrompter


@pytest.fixture
def fixture_ssh_dir(tmp_path: Path) -> Path:
    """The fixture .ssh directory copied to a temp dir, every file and dir 0777."""
    dest = tmp_path / "ssh"
    shutil.copytree(tb.SSH_FIXTURE_DIR, dest)
    for root, dirs, files in os.walk(dest):
        for name in (*dirs, *files):
            Path(root, name).chmod(0o777)
    dest.chmod(0o777)
    return dest


@pytest.fixture
def portkey_config(tmp_path: Path, fixture_ssh_dir: Path) -> Config:
    data, keys = tmp_path / "data", tmp_path / "keys-generated"
    data.mkdir()
    keys.mkdir()
    return Config(
        data_dir=data,
        ssh_dir=fixture_ssh_dir,
        keys_dir=keys,
        ssh_dir_display="C:\\Users\\john\\.ssh",
        keys_dir_display="C:\\Users\\john\\.ssh\\port-key",
        public_port=8822,
        allowed_hosts=("localhost:8822", "127.0.0.1:8822", "[::1]:8822"),
    )


# --- asyncssh helpers --------------------------------------------------------------------------


@pytest.fixture
def ssh_connect() -> SshConnect:
    """``tests.fixtures.sshclient.ssh_connect``: see its docstring."""
    return sshclient.ssh_connect


@pytest.fixture
def reset_ssh_user(totp: tb.Totp) -> Callable[..., Awaitable[str]]:
    """``await reset_ssh_user("fresh")`` (keyword ``server=``/``tunnel=`` for other sshds)."""

    async def reset(user: str, **kwargs: Any) -> str:
        return await sshclient.reset_user(user, totp=totp, **kwargs)

    return reset
