"""The remote script's text: the key line is the only variable part, and no comment or key
text can break out of its quoting. Hostile comments are also run for real under each shell."""

from __future__ import annotations

import random
import re
import string
from pathlib import Path

import pytest

from portkey.wizard.remote_script import (
    MARK_ADDED,
    MARK_BEGIN,
    build_install_script,
    clean_comment,
    clip_output,
    manual_install_command,
    parse_key_line,
    parse_script_output,
    write_failure_reason,
)
from tests.unit.wizard.conftest import SHELLS, RunSh

SAFE_COMMENT = re.compile(r"[A-Za-z0-9@._ -]*")
VARIABLE = re.compile(r"^PORTKEY_KEY_(TYPE|BLOB|LINE)='[A-Za-z0-9@._ +/=-]+'$")

HOSTILE_COMMENTS = [
    "x'; touch \"$HOME/pwned\"; echo '",
    "$(touch $HOME/pwned)",
    "`touch $HOME/pwned`",
    "a\nPORTKEY_ADDED\ntouch $HOME/pwned",
    "a\r\ntouch pwned",
    "'\"'\"'; touch pwned #",
    "\\'; touch pwned; \\",
    "%s%n%x %%",
    "${HOME:?boom}",
    "john@laptop ; rm -rf ~ &",
    "naïve – 日本語 🙂 key",
    "\x00\x07\x1b[31mred\x1b[0m",
    "-n -e --",
    "*" * 300,
    "   ",
]


def key_parts(line: str) -> tuple[str, str]:
    parsed = parse_key_line(line)
    return parsed.key_type, parsed.blob


# --- the key line ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("john@laptop", "john@laptop"),
        ("TEST-ONLY port-key fixture id_rsa", "TEST-ONLY port-key fixture id_rsa"),
        ("  two   spaces\tand tab  ", "two spaces and tab"),
        ("it's mine", "its mine"),
        ("a\r\nb", "ab"),
        ("naïve 日本", "nave"),
        ("$(id)`x`;|&<>", "idx"),
        ("", ""),
    ],
)
def test_clean_comment(raw: str, clean: str) -> None:
    assert clean_comment(raw) == clean


def test_clean_comment_fuzz() -> None:
    rng = random.Random(6)
    alphabet = string.printable + "\x00\x7f'\"`$\\äé🙂 \u0085"
    for _ in range(3000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        cleaned = clean_comment(raw)
        assert SAFE_COMMENT.fullmatch(cleaned)
        assert cleaned == cleaned.strip()
        assert "  " not in cleaned
        assert len(cleaned) <= 100


def test_parse_key_line(key_line: str) -> None:
    parsed = parse_key_line(key_line)
    assert parsed.key_type == "ssh-ed25519"
    assert parsed.comment == "TEST-ONLY wizard key"
    assert parsed.line == key_line
    assert parsed.key_id == " ".join(key_line.split()[:2])
    assert parse_key_line(parsed.key_id).line == parsed.key_id  # no comment at all


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t, b: "",
        lambda t, b: t,
        lambda t, b: f"{t}' {b}",
        lambda t, b: f"{t};id {b}",
        lambda t, b: f"{t} {b}'",
        lambda t, b: f"{t} {b[:-4]}$(id)",
        lambda t, b: f"{t} {b[:-2]}",  # truncated base64
        lambda t, b: f"ssh-rsa {b}",  # type does not match the blob
        lambda t, b: f"{t} QUJD",  # base64, but not a key blob
        lambda t, b: f'command="id" {t} {b}',  # options are not accepted here
    ],
)
def test_parse_key_line_refuses_bad_type_or_data(key_line: str, mangle: object) -> None:
    t, b = key_parts(key_line)
    bad = mangle(t, b)  # type: ignore[operator]
    with pytest.raises(ValueError, match="key") as err:
        parse_key_line(bad)
    assert "'" not in str(err.value) and "$(" not in str(err.value)
    with pytest.raises(ValueError, match="key"):
        build_install_script(bad)


# --- the script text --------------------------------------------------------------------------


def test_only_the_three_quoted_variables_differ(key_line: str, other_key_line: str) -> None:
    def constant_part(script: str) -> list[str]:
        return [ln for ln in script.splitlines() if not ln.startswith("PORTKEY_KEY_")]

    base = build_install_script(key_line)
    t, b = key_parts(key_line)
    for comment in [*HOSTILE_COMMENTS, ""]:
        script = build_install_script(f"{t} {b} {comment}")
        assert constant_part(script) == constant_part(base)
        variables = [ln for ln in script.splitlines() if ln.startswith("PORTKEY_KEY_")]
        assert len(variables) == 3
        assert all(VARIABLE.fullmatch(v) for v in variables), variables
    assert constant_part(build_install_script(other_key_line)) == constant_part(base)


def test_script_shape(key_line: str) -> None:
    script = build_install_script(key_line)
    assert script.splitlines()[-1] == "portkey_install </dev/null"
    assert "umask 077" in script
    assert "mkdir -m 700" in script
    assert "chmod 600" in script
    assert "chmod 7" not in script.replace("mkdir -m 700", "")  # never changes existing modes
    assert script.isascii()


def test_manual_install_command(key_line: str) -> None:
    t, b = key_parts(key_line)
    command = manual_install_command(f"{t} {b} it's $(mine)")
    assert command == (
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{t} {b} its mine' >> "
        "~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )


@pytest.mark.parametrize("shell", SHELLS)
def test_hostile_comments_under_each_shell(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str
) -> None:
    t, b = key_parts(key_line)
    for i, comment in enumerate(HOSTILE_COMMENTS):
        root = tmp_path / f"case{i}"
        home = root / "home"
        home.mkdir(parents=True, mode=0o750)
        proc = run_sh(shell, home, build_install_script(f"{t} {b} {comment}"), env={"PWD": "/"})
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [MARK_BEGIN, MARK_ADDED], (comment, proc.stdout)
        expected = f"{t} {b} {clean_comment(comment)}".rstrip()
        assert (home / ".ssh" / "authorized_keys").read_text() == expected + "\n"
        assert sorted(p.name for p in home.iterdir()) == [".ssh"]
        assert sorted(p.name for p in (home / ".ssh").iterdir()) == ["authorized_keys"]
        assert not list(tmp_path.rglob("pwned")), comment


# --- reading the output -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "stderr", "status", "outcome", "warn"),
    [
        ("PORTKEY_BEGIN\nPORTKEY_ADDED\n", "", 0, "added", False),
        ("PORTKEY_BEGIN\r\nPORTKEY_ALREADY\r\nPORTKEY_WARN_PERMS\r\n", "", 0, "already_present", True),
        ("PORTKEY_BEGIN\nPORTKEY_ADDED\nPORTKEY_WARN_PERMS\n", "", 0, "added", True),
        ("PORTKEY_BEGIN\nPORTKEY_READONLY\n", "mkdir: Read-only file system", 0, "readonly", False),
        ("PORTKEY_BEGIN\nPORTKEY_FAILED\n", "", 0, "failed", False),
        ("PORTKEY_BEGIN\n", "", None, "failed", False),  # cut off
        ("This account is currently not available.\n", "", 1, "non_posix", False),
        ("", "sh: command not found", 127, "non_posix", False),
        ("", "'sh' is not recognized as an internal or external command,", 1, "non_posix", False),
        ("", "", None, "non_posix", False),  # the server would not run a command
        ("PORTKEY_ADDED\n", "", 0, "non_posix", False),  # no BEGIN: not our script
    ],
)
def test_parse_script_output(
    stdout: str, stderr: str, status: int | None, outcome: str, warn: bool
) -> None:
    result = parse_script_output(stdout, stderr, status)
    assert (result.outcome, result.perms_warning) == (outcome, warn)
    assert "PORTKEY_" not in result.output


def test_output_keeps_remote_text_and_is_clipped() -> None:
    result = parse_script_output("motd line\nPORTKEY_BEGIN\nPORTKEY_ADDED\n", "warning: x", 0)
    assert result.output == "motd line\nwarning: x"
    big = parse_script_output("é" * 5000, "", 1)
    assert len(big.output.encode()) <= 4096
    assert clip_output("abc", 2) == "ab"
    assert clip_output("é", 1) == ""


def test_write_failure_reason() -> None:
    assert write_failure_reason("sh: 1: cannot create x: Read-only file system") == (
        "Read-only file system"
    )
    assert write_failure_reason("mkdir: cannot create directory: Permission denied") == (
        "Permission denied"
    )
    assert write_failure_reason("something else") is None
