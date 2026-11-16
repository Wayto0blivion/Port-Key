"""The POSIX ``sh`` script that installs a public key (package 6).

Run as ``sh -s`` with the script on stdin, so any login shell (fish, csh, zsh) only has to parse
``sh -s``. What it does (docs/architecture.md section 8):

- ``umask 077``; create ``~/.ssh`` (``mkdir -m 700``) and ``authorized_keys`` (600) only when
  they are missing; existing modes are never changed;
- if the exact key (``<type> <base64>``, on a line that is not a comment, with or without
  options or a comment, CRLF tolerated) is already there, print ``PORTKEY_ALREADY`` and write
  nothing;
- otherwise add a newline if the file does not end in one, append the key line, check it is
  now there, and print ``PORTKEY_ADDED``;
- run ``restorecon`` if it exists and something was created or appended;
- print ``PORTKEY_WARN_PERMS`` when home, ``~/.ssh`` or the file is group- or other-writable
  (sshd's StrictModes then ignores the file);
- print ``PORTKEY_READONLY`` when a directory or the file cannot be written.

``PORTKEY_BEGIN`` comes first: output without it means ``sh`` never ran the script (Windows
cmd or PowerShell, nologin, a forced command), which is ``non_posix``.

The key line is the only variable part. ``parse_key_line`` accepts only an algorithm name of
``[A-Za-z0-9@._-]``, base64 whose decoded blob names that same algorithm, and a comment cleaned
to ``[A-Za-z0-9@._ -]``. None of those characters can end a single-quoted shell string or a
line, so whatever a key file's comment says, the script text around the line never changes.
``FIX_PERMS_SCRIPT`` runs only when the user picks "Fix permissions".
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from typing import Literal

MARK_BEGIN = "PORTKEY_BEGIN"
MARK_ALREADY = "PORTKEY_ALREADY"
MARK_ADDED = "PORTKEY_ADDED"
MARK_WARN_PERMS = "PORTKEY_WARN_PERMS"
MARK_READONLY = "PORTKEY_READONLY"
MARK_FAILED = "PORTKEY_FAILED"
MARKERS = (MARK_BEGIN, MARK_ALREADY, MARK_ADDED, MARK_WARN_PERMS, MARK_READONLY, MARK_FAILED)

FIX_PERMS_SCRIPT = "chmod go-w ~ ~/.ssh ~/.ssh/authorized_keys\n"

#: Details shown in the UI's collapsed block are capped at this many bytes.
OUTPUT_LIMIT = 4096
COMMENT_LIMIT = 100

_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._-]{0,63}")
_BLOB = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_COMMENT_DROP = re.compile(r"[^A-Za-z0-9@._ -]")
_SPACES = re.compile(r" +")
_SAFE_LINE = re.compile(r"[A-Za-z0-9@._ +/=-]+")


@dataclass(frozen=True, slots=True)
class ScriptResult:
    outcome: Literal["added", "already_present", "readonly", "non_posix", "failed"]
    perms_warning: bool
    output: str  # combined stdout/stderr, truncated to 4 KB, for the "Details" block


@dataclass(frozen=True, slots=True)
class KeyLine:
    """A public key reduced to what the script may contain."""

    key_type: str
    blob: str
    comment: str

    @property
    def key_id(self) -> str:
        """``<type> <base64>``: what the script looks for."""
        return f"{self.key_type} {self.blob}"

    @property
    def line(self) -> str:
        """The line appended to authorized_keys."""
        return f"{self.key_id} {self.comment}" if self.comment else self.key_id


def clean_comment(comment: str) -> str:
    """Keep only ``[A-Za-z0-9@._ -]``; no CR or LF ever survives. Runs of spaces collapse, the
    ends are trimmed and the result is at most 100 characters."""
    kept = _COMMENT_DROP.sub("", comment.replace("\t", " "))
    return _SPACES.sub(" ", kept).strip()[:COMMENT_LIMIT].strip()


def _blob_type(blob: str) -> str | None:
    """The algorithm name stored at the start of an SSH public key blob."""
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 4:
        return None
    (size,) = struct.unpack(">I", raw[:4])
    if size == 0 or size > 64 or len(raw) < 4 + size:
        return None
    try:
        return raw[4 : 4 + size].decode("ascii")
    except UnicodeDecodeError:
        return None


def parse_key_line(public_key_line: str) -> KeyLine:
    """Split ``<type> <base64> [comment]`` and check every part. Raises ``ValueError`` (with a
    message that never repeats the input) when the type or the key data is not acceptable."""
    parts = public_key_line.strip().split(None, 2)
    if len(parts) < 2:
        raise ValueError("not an OpenSSH public key line")
    key_type, blob = parts[0], parts[1]
    if not _TYPE.fullmatch(key_type) or not _BLOB.fullmatch(blob):
        raise ValueError("the key type or key data has characters a public key cannot have")
    if _blob_type(blob) != key_type:
        raise ValueError("the key data does not match its key type")
    key = KeyLine(key_type, blob, clean_comment(parts[2] if len(parts) > 2 else ""))
    if not _SAFE_LINE.fullmatch(key.line):  # belt and braces: the quoting relies on this
        raise ValueError("the key line has characters that are not allowed")
    return key


def _quote(value: str) -> str:
    """Single-quote ``value``; only ever called with ``_SAFE_LINE`` text (no quote inside)."""
    if not _SAFE_LINE.fullmatch(value):
        raise ValueError("refusing to quote unchecked text")
    return f"'{value}'"


_BODY = r"""portkey_has_key() {
    if command -v awk >/dev/null 2>&1; then
        awk -v t="$PORTKEY_KEY_TYPE" -v b="$PORTKEY_KEY_BLOB" '
            { sub(/\r$/, "") }
            /^[ \t]*#/ { next }
            { for (i = 1; i < NF; i++) if ($i == t && $(i + 1) == b) f = 1 }
            END { exit !f }' "$1"
    else
        grep -v '^[[:space:]]*#' "$1" | grep -qF -e "$PORTKEY_KEY_TYPE $PORTKEY_KEY_BLOB"
    fi
}

portkey_install() {
    echo PORTKEY_BEGIN
    umask 077
    if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
        echo "Port-Key: this account has no home directory." >&2
        echo PORTKEY_FAILED
        return 0
    fi
    d="$HOME/.ssh"
    f="$d/authorized_keys"
    changed=
    if [ ! -d "$d" ]; then
        if ! mkdir -m 700 "$d"; then
            echo PORTKEY_READONLY
            return 0
        fi
        changed=1
    fi
    if [ ! -e "$f" ]; then
        if ! printf '' >> "$f"; then
            echo PORTKEY_READONLY
            return 0
        fi
        chmod 600 "$f"
        changed=1
    fi
    if portkey_has_key "$f"; then
        echo PORTKEY_ALREADY
    else
        if [ -s "$f" ] && [ -n "$(tail -c 1 "$f")" ]; then
            if ! printf '\n' >> "$f"; then
                echo PORTKEY_READONLY
                return 0
            fi
        fi
        if ! printf '%s\n' "$PORTKEY_KEY_LINE" >> "$f"; then
            echo PORTKEY_READONLY
            return 0
        fi
        if ! portkey_has_key "$f"; then
            echo "Port-Key: the key is not in the file after writing it." >&2
            echo PORTKEY_FAILED
            return 0
        fi
        changed=1
        echo PORTKEY_ADDED
    fi
    if [ -n "$changed" ] && command -v restorecon >/dev/null 2>&1; then
        restorecon -F "$d" "$f" >/dev/null 2>&1 || :
    fi
    for p in "$HOME" "$d" "$f"; do
        m=$(ls -ldL "$p" 2>/dev/null) || continue
        case "$m" in
            ?????w*|????????w*)
                echo PORTKEY_WARN_PERMS
                break
                ;;
        esac
    done
    return 0
}

portkey_install </dev/null
"""


def build_install_script(public_key_line: str) -> str:
    """The script text for one key line (``<type> <base64> [comment]``). Raises
    ``ValueError`` for a line ``parse_key_line`` refuses."""
    key = parse_key_line(public_key_line)
    head = (
        "# Port-Key: add one public key to ~/.ssh/authorized_keys (run as: sh -s)\n"
        f"PORTKEY_KEY_TYPE={_quote(key.key_type)}\n"
        f"PORTKEY_KEY_BLOB={_quote(key.blob)}\n"
        f"PORTKEY_KEY_LINE={_quote(key.line)}\n"
    )
    return head + _BODY


def manual_install_command(public_key_line: str) -> str:
    """The copyable command from the UX spec:
    ``mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '<key>' >> ~/.ssh/authorized_keys &&
    chmod 600 ~/.ssh/authorized_keys``."""
    line = parse_key_line(public_key_line).line
    return (
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo {_quote(line)} >> ~/.ssh/authorized_keys"
        " && chmod 600 ~/.ssh/authorized_keys"
    )


def clip_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """``text`` cut to at most ``limit`` UTF-8 bytes (never in the middle of a character)."""
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def _details(stdout: str, stderr: str) -> str:
    lines = [ln for ln in stdout.replace("\r", "").split("\n") if ln.strip() not in MARKERS]
    out = "\n".join(lines).strip("\n")
    err = stderr.replace("\r", "").strip("\n")
    return clip_output("\n".join(part for part in (out, err) if part))


def parse_script_output(stdout: str, stderr: str, exit_status: int | None) -> ScriptResult:
    """Map markers and exit status to an outcome. ``sh`` missing (127, Windows cmd/PowerShell
    errors, forced commands, nologin) -> ``non_posix``; a directory or file that cannot be
    written ("Read-only file system", "Permission denied") -> ``readonly``."""
    marks = {ln.strip() for ln in stdout.replace("\r", "").split("\n")}
    output = _details(stdout, stderr)
    warn = MARK_WARN_PERMS in marks
    if MARK_BEGIN not in marks:
        return ScriptResult("non_posix", False, output)
    if MARK_READONLY in marks:
        return ScriptResult("readonly", warn, output)
    if MARK_ALREADY in marks:
        return ScriptResult("already_present", warn, output)
    if MARK_ADDED in marks:
        return ScriptResult("added", warn, output)
    return ScriptResult("failed", warn, output)


_REASONS = (
    "Read-only file system",
    "Permission denied",
    "Operation not permitted",
    "No space left on device",
    "Disk quota exceeded",
)


def write_failure_reason(output: str) -> str | None:
    """The system's reason for a failed write, if the output names a known one."""
    lowered = output.lower()
    for reason in _REASONS:
        if reason.lower() in lowered:
            return reason
    return None
