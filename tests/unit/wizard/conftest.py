"""Fixtures for package 6's unit tests; the fakes they use are in ``fakes.py`` and the wired
wizard and run helpers in ``world.py``.

- ``db``: a migrated SQLite database in a temp dir.
- ``world(*attempts)``: ``world.make_world`` on ``db``: an ``InstallWizard`` over the fake SSH
  layer, whose connector plays ``attempts`` in order.
- ``key_line``: a generated ED25519 public key line with a comment (same key all session).
- ``other_key_line``: a second, different key line.
- ``busybox_bin``: a directory of busybox applet links, so a script run with only it on PATH
  uses busybox's own awk, tail, ls, mkdir and chmod (as on Alpine or a router).
- ``run_sh(shell, home, script)``: run ``script`` as ``<shell> -s`` with HOME set; shell is
  ``dash``, ``bash`` or ``busybox``.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path

import asyncssh
import pytest

from portkey.store.db import Database
from tests.unit.wizard.fakes import Attempt
from tests.unit.wizard.world import World, make_world

SHELLS = ("dash", "bash", "busybox")
RunSh = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "portkey.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def world(db: Database) -> Callable[..., World]:
    def make(*attempts: Attempt) -> World:
        return make_world(db, *attempts)

    return make


@cache
def _public_line(name: str) -> str:
    key = asyncssh.generate_private_key("ssh-ed25519")
    type_, blob = bytes(key.export_public_key("openssh")).decode().split()[:2]
    return f"{type_} {blob} TEST-ONLY wizard {name}"


@pytest.fixture(scope="session")
def key_line() -> str:
    return _public_line("key")


@pytest.fixture(scope="session")
def other_key_line() -> str:
    return _public_line("other")


@pytest.fixture(scope="session")
def busybox_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    busybox = shutil.which("busybox")
    assert busybox, "the dev image must have busybox (tests/unit/skeleton/test_dev_image.py)"
    applets = subprocess.run(
        [busybox, "--list"], capture_output=True, text=True, check=True
    ).stdout.split()
    out = tmp_path_factory.mktemp("busybox-bin")
    for applet in applets:
        (out / applet).symlink_to(busybox)
    return out


@pytest.fixture
def run_sh(busybox_bin: Path) -> RunSh:
    def run(
        shell: str, home: Path | None, script: str, *, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if shell == "busybox":
            argv, path = [str(busybox_bin / "sh"), "-s"], str(busybox_bin)
        else:
            exe = shutil.which(shell)
            assert exe, f"the dev image must have {shell}"
            argv, path = [exe, "-s"], "/usr/local/bin:/usr/bin:/bin"
        environ = {"PATH": path, "LC_ALL": "C"}
        if home is not None:
            environ["HOME"] = str(home)
        environ.update(env or {})
        return subprocess.run(
            argv, input=script, capture_output=True, text=True, env=environ, timeout=30
        )

    return run
