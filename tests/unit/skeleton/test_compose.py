"""The owner's compose.yaml keeps the container hardening docs/architecture.md section 3 lists,
pulls the release image, and compose.build.yaml builds the same service from source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
RELEASE_IMAGE = "ghcr.io/wayto0blivion/port-key"


def compose(name: str = "compose.yaml") -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO / name).read_text())
    return data


def service() -> dict[str, Any]:
    svc: dict[str, Any] = compose()["services"]["portkey"]
    return svc


def test_published_on_loopback_only() -> None:
    assert service()["ports"] == ["127.0.0.1:8822:8000"]


def test_read_only_without_capabilities() -> None:
    svc = service()
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]


def test_the_readme_names_the_volume_docker_really_creates() -> None:
    # Review finding: the README said `portkey-data`, but compose prefixes the project name.
    data = compose()
    [volume] = data["volumes"]
    real = f"{data['name']}_{volume}"
    readme = (REPO / "README.md").read_text()
    assert f"Docker volume `{real}`" in readme
    # Backup and restore go through the compose service, so they use whichever image (pulled
    # or built) and volume compose.yaml names, and never a hard-coded image tag.
    assert readme.count("docker compose run --rm --no-deps --entrypoint") == 2
    assert "portkey:local" not in readme.split("## Build from source", 1)[0]


def test_memory_and_process_limits() -> None:
    # Security review: without them one runaway request or command could take the whole
    # Docker Desktop VM's memory, and with it every open terminal session.
    svc = service()
    assert svc["mem_limit"] == "1g"
    assert 64 <= int(svc["pids_limit"]) <= 1024


def test_compose_yaml_pulls_the_release_image_and_builds_nothing() -> None:
    # The one file a user downloads: a build section would need the source tree next to it.
    svc = service()
    assert "build" not in svc
    assert svc["image"] == f"{RELEASE_IMAGE}:${{PORTKEY_VERSION:-latest}}"


def test_the_build_override_builds_the_runtime_under_a_local_tag() -> None:
    override = compose("compose.build.yaml")
    assert list(override["services"]) == ["portkey"]
    svc = override["services"]["portkey"]
    assert svc["build"] == {"context": ".", "target": "runtime"}
    # A local build must never carry the release image's name.
    assert svc["image"] == "portkey:local"
    assert not svc["image"].startswith(RELEASE_IMAGE)
    assert set(svc) == {"build", "image"}  # hardening and mounts come from compose.yaml
