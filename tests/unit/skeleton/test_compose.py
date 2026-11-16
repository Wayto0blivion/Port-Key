"""The owner's compose.yaml keeps the container hardening docs/architecture.md section 3 lists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]


def service() -> dict[str, Any]:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    svc: dict[str, Any] = compose["services"]["portkey"]
    return svc


def test_published_on_loopback_only() -> None:
    assert service()["ports"] == ["127.0.0.1:8822:8000"]


def test_read_only_without_capabilities() -> None:
    svc = service()
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]


def test_memory_and_process_limits() -> None:
    # Security review: without them one runaway request or command could take the whole
    # Docker Desktop VM's memory, and with it every open terminal session.
    svc = service()
    assert svc["mem_limit"] == "1g"
    assert 64 <= int(svc["pids_limit"]) <= 1024
