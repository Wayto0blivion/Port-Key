"""Scan the key folders: /keys-generated, /ssh/port-key and /ssh (top level only).

Rules:
- Nothing outside the configured ``ssh_dir`` and ``keys_dir`` is ever read: a symlink (file or
  folder) that resolves outside them is reported, not followed.
- File modes do not matter (bind mounts from Windows show 0777).
- Keys are deduplicated by SHA256 fingerprint; the first root wins, so a key that is in
  /keys-generated (the same Windows folder as /ssh/port-key) is listed once, as ``generated``.
- ``.ppk`` files, damaged or unsupported keys and ``.pub`` files without their private key are
  problems. Files that are not keys at all (config, known_hosts, notes) are ignored silently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from portkey.config import Config, join_display
from portkey.keys._parse import ParsedKey, ParseProblem, parse_companion, parse_private_key
from portkey.keys.textio import read_text_normalized
from portkey.models import KeyProblem, KeySource

MAX_KEY_BYTES = 64 * 1024
IGNORED_NAMES = frozenset(
    {
        "known_hosts",
        "known_hosts.old",
        "authorized_keys",
        "authorized_keys2",
        "config",
        "environment",
        "rc",
        "desktop.ini",
        "thumbs.db",
    }
)


@dataclass(frozen=True, slots=True)
class ScanRoot:
    path: Path
    display: str
    source: KeySource


@dataclass(frozen=True, slots=True)
class FoundKey:
    parsed: ParsedKey
    name: str
    path: Path  # container path
    display_path: str
    source: KeySource


@dataclass(slots=True)
class ScanResult:
    keys: list[FoundKey] = field(default_factory=list)
    problems: list[KeyProblem] = field(default_factory=list)


def scan_roots(config: Config) -> list[ScanRoot]:
    """Highest priority first (wins on duplicate fingerprints)."""
    return [
        ScanRoot(config.keys_dir, config.keys_dir_display, "generated"),
        ScanRoot(
            config.ssh_dir / "port-key",
            join_display(config.ssh_dir_display, "port-key"),
            "generated",
        ),
        ScanRoot(config.ssh_dir, config.ssh_dir_display, "ssh_dir"),
    ]


class _Fence:
    """Decides whether a resolved path is inside one of the allowed folders."""

    def __init__(self, config: Config) -> None:
        self.allowed = [_resolve(p) for p in (config.ssh_dir, config.keys_dir)]

    def inside(self, path: Path) -> bool:
        real = _resolve(path)
        return any(real == root or real.is_relative_to(root) for root in self.allowed)


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def read_key_text(path: Path) -> str | None:
    """The normalised text of a candidate file, or None for anything that is not a text file
    of key size. ``OSError`` (permission, I/O) propagates."""
    try:
        return read_text_normalized(path, MAX_KEY_BYTES)
    except (ValueError, UnicodeDecodeError):
        return None


def scan(config: Config) -> ScanResult:
    result = ScanResult()
    fence = _Fence(config)
    seen_dirs: set[Path] = set()
    seen_fps: set[str] = set()
    seen_problems: set[str] = set()
    orphans: list[tuple[str, str]] = []  # (display path, pub text)

    for root in scan_roots(config):
        if not root.path.is_dir() or not fence.inside(root.path):
            continue
        real = _resolve(root.path)
        if real in seen_dirs:
            continue
        seen_dirs.add(real)
        _scan_dir(root, fence, result, seen_fps, seen_problems, orphans)

    for display, pub_text in orphans:
        companion = parse_companion(pub_text)
        if companion is None or companion[0].fingerprint in seen_fps:
            continue
        _problem(
            result,
            seen_problems,
            display,
            ParseProblem(
                "not_private_key", "Public key only. The private key is not in this folder."
            ),
        )
    return result


def _scan_dir(
    root: ScanRoot,
    fence: _Fence,
    result: ScanResult,
    seen_fps: set[str],
    seen_problems: set[str],
    orphans: list[tuple[str, str]],
) -> None:
    try:
        with os.scandir(root.path) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return

    files: dict[str, Path] = {}  # lower-case name -> path
    names: dict[str, str] = {}  # lower-case name -> real name
    for entry in entries:
        if entry.name.startswith("."):
            continue
        path = Path(entry.path)
        display = join_display(root.display, entry.name)
        try:
            if entry.is_symlink() and not fence.inside(path):
                if not entry.is_file(follow_symlinks=True):
                    continue  # a linked folder outside: not scanned, not a key file
                _problem(
                    result,
                    seen_problems,
                    display,
                    ParseProblem(
                        "unreadable",
                        "A link to a file outside the .ssh folder. Port-Key does not follow it.",
                    ),
                )
                continue
            if not entry.is_file(follow_symlinks=True):
                continue
        except OSError:
            continue
        lower = entry.name.lower()
        if lower in IGNORED_NAMES:
            continue
        files[lower] = path
        names[lower] = entry.name

    private_stems = {n for n in files if not n.endswith(".pub")}
    for lower, path in files.items():
        display = join_display(root.display, names[lower])
        if lower.endswith(".pub"):
            if lower.removesuffix(".pub") not in private_stems:
                pub_text = _read_quiet(path)
                if pub_text is not None:
                    orphans.append((display, pub_text))
            continue
        if lower.endswith(".ppk"):
            _problem(
                result, seen_problems, display, ParseProblem("ppk", "PuTTY format, not supported")
            )
            continue
        try:
            text = read_key_text(path)
        except OSError:
            _problem(
                result, seen_problems, display, ParseProblem("unreadable", "Port-Key can't read it")
            )
            continue
        if text is None:
            continue
        pub_path = files.get(f"{lower}.pub")
        pub_text = _read_quiet(pub_path) if pub_path is not None else None
        try:
            parsed = parse_private_key(text, pub_text)
        except ParseProblem as problem:
            _problem(result, seen_problems, display, problem)
            continue
        if parsed is None or parsed.fp in seen_fps:
            continue
        seen_fps.add(parsed.fp)
        result.keys.append(FoundKey(parsed, names[lower], path, display, root.source))


def _read_quiet(path: Path) -> str | None:
    try:
        return read_key_text(path)
    except OSError:
        return None


def _problem(result: ScanResult, seen: set[str], display: str, problem: ParseProblem) -> None:
    if display in seen:
        return
    seen.add(display)
    result.problems.append(KeyProblem(display, problem.reason, problem.detail))
