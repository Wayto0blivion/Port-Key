"""Fakes and fixtures for the diagnostics tests (package 7)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from portkey.models import KeyProblem, KeyRecord
from portkey.services import Services


class FakeKeys:
    """Stands in for the keys package's ``KeyProvider`` (only what diagnostics reads)."""

    def __init__(
        self,
        keys: list[KeyRecord] | None = None,
        problems: list[KeyProblem] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.keys = keys or []
        self.problem_list = problems or []
        self.error = error

    def list_keys(self) -> list[KeyRecord]:
        if self.error:
            raise self.error
        return list(self.keys)

    def problems(self) -> list[KeyProblem]:
        if self.error:
            raise self.error
        return list(self.problem_list)


def make_key(name: str, source: str = "ssh_dir", fp_char: str = "A") -> KeyRecord:
    return KeyRecord(
        fp="SHA256:" + fp_char * 43,
        name=name,
        type="ed25519",
        bits=256,
        comment="john@WORKPC",
        path=Path("/ssh") / name,
        display_path=f"C:\\Users\\john\\.ssh\\{name}",
        source=source,  # type: ignore[arg-type]
        encrypted=False,
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample john@WORKPC",
    )


@pytest.fixture
def use_keys(services: Services) -> Callable[..., FakeKeys]:
    def install(**kwargs: object) -> FakeKeys:
        fake = FakeKeys(**kwargs)  # type: ignore[arg-type]
        services.keys = fake  # type: ignore[assignment]
        return fake

    return install
