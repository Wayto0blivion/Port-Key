"""Release plumbing (.github/workflows/release.yml): one version everywhere, a CHANGELOG section
for it, the image labels, and third-party actions pinned to commits."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import portkey
from portkey.config import Config

REPO = Path(__file__).resolve().parents[3]


def pyproject_version() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    version: str = data["project"]["version"]
    return version


def test_the_app_reports_the_pyproject_version() -> None:
    # The release workflow refuses a tag that is not "v" + this version; the app's health,
    # settings and Diagnostics all report portkey.__version__ (through Config.version).
    version = pyproject_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert portkey.__version__ == version
    assert Config().version == version


def test_the_changelog_has_a_section_for_the_version() -> None:
    changelog = (REPO / "CHANGELOG.md").read_text()
    version = re.escape(pyproject_version())
    assert re.search(rf"^## \[?{version}\]?( |$)", changelog, re.MULTILINE)


def test_the_runtime_image_carries_the_oci_labels() -> None:
    runtime = (REPO / "Dockerfile").read_text().split("AS runtime", 1)[1]
    for label in ("source", "licenses", "title", "description", "version", "revision"):
        assert f"org.opencontainers.image.{label}=" in runtime
    assert 'source="https://github.com/Wayto0blivion/Port-Key"' in runtime
    assert 'licenses="MIT"' in runtime


def test_actions_are_pinned_to_full_commit_shas() -> None:
    uses = []
    for workflow in (REPO / ".github" / "workflows").glob("*.y*ml"):
        uses += re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", workflow.read_text(), re.MULTILINE)
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d+\.\d+\.\d+", comment), f"{ref} needs a version comment"
