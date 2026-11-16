"""The dev/test image carries what later packages' unit tests rely on.

Package 6 runs the remote install script for real under dash, bash and busybox sh
(docs/architecture.md section 10), so all three must be on PATH in ``portkey-dev:local``.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


@pytest.mark.parametrize(
    "argv",
    [["dash", "-c", "echo ok"], ["bash", "-c", "echo ok"], ["busybox", "sh", "-c", "echo ok"]],
    ids=["dash", "bash", "busybox-sh"],
)
def test_posix_shells_are_available(argv: list[str]) -> None:
    assert shutil.which(argv[0]), f"{argv[0]} is missing from the dev image (see Dockerfile)"
    out = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout == "ok\n"
