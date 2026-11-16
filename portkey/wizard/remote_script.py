"""The POSIX ``sh`` script that installs a public key (package 6).

Run as ``sh -s`` with the script on stdin, so any login shell (fish, csh, zsh) only has to parse
``sh -s``. Outline (docs/architecture.md section 8): ``umask 077``; create ``~/.ssh`` (700) and
``authorized_keys`` (600) if missing; ``grep -qF '<type> <base64>'`` -> print ``PORTKEY_ALREADY``;
otherwise add a trailing newline if missing and append the single-quoted key line; run
``restorecon`` if present; ``find -perm -022`` on home, ``.ssh`` and the file ->
``PORTKEY_WARN_PERMS``; print ``PORTKEY_ADDED``. The key comment is cleaned to
``[A-Za-z0-9@._ -]``. Existing modes are never changed silently; ``FIX_PERMS_SCRIPT`` runs only
when the user picks "Fix permissions".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MARK_ALREADY = "PORTKEY_ALREADY"
MARK_ADDED = "PORTKEY_ADDED"
MARK_WARN_PERMS = "PORTKEY_WARN_PERMS"
MARK_READONLY = "PORTKEY_READONLY"

FIX_PERMS_SCRIPT = "chmod go-w ~ ~/.ssh ~/.ssh/authorized_keys\n"


@dataclass(frozen=True, slots=True)
class ScriptResult:
    outcome: Literal["added", "already_present", "readonly", "non_posix", "failed"]
    perms_warning: bool
    output: str  # combined stdout/stderr, truncated to 4 KB, for the "Details" block


def clean_comment(comment: str) -> str:
    """Keep only ``[A-Za-z0-9@._ -]``; no CR or LF ever survives."""
    raise NotImplementedError("package 6")


def build_install_script(public_key_line: str) -> str:
    """The script text for one key line (``<type> <base64> [comment]``)."""
    raise NotImplementedError("package 6")


def manual_install_command(public_key_line: str) -> str:
    """The copyable command from the UX spec:
    ``mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '<key>' >> ~/.ssh/authorized_keys &&
    chmod 600 ~/.ssh/authorized_keys``."""
    raise NotImplementedError("package 6")


def parse_script_output(stdout: str, stderr: str, exit_status: int | None) -> ScriptResult:
    """Map markers and exit status to an outcome. ``sh`` missing (127, Windows cmd/PowerShell
    errors, forced commands) -> ``non_posix``; "Read-only file system" -> ``readonly``."""
    raise NotImplementedError("package 6")
