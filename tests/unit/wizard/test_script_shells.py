"""The remote script, run for real under dash, bash and busybox sh against a temporary HOME.

busybox runs with only its own applets on PATH, so awk, tail and ls are busybox's too.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from portkey.wizard.remote_script import (
    FIX_PERMS_SCRIPT,
    build_install_script,
    parse_key_line,
    parse_script_output,
)
from tests.unit.wizard.conftest import SHELLS, RunSh

pytestmark = pytest.mark.parametrize("shell", SHELLS)

not_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def home_dir(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(mode=0o750)
    home.chmod(0o750)
    return home


def install(run_sh: RunSh, shell: str, home: Path, line: str) -> tuple[set[str], str]:
    proc = run_sh(shell, home, build_install_script(line))
    assert proc.returncode == 0, proc.stderr
    marks = {ln for ln in proc.stdout.splitlines() if ln.startswith("PORTKEY_")}
    assert set(proc.stdout.splitlines()) == marks, proc.stdout  # nothing but markers
    return marks, proc.stderr


def ssh_files(home: Path) -> tuple[Path, Path]:
    return home / ".ssh", home / ".ssh" / "authorized_keys"


def test_fresh_home(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    home = home_dir(tmp_path)
    marks, _ = install(run_sh, shell, home, key_line)
    assert marks == {"PORTKEY_BEGIN", "PORTKEY_ADDED"}
    d, f = ssh_files(home)
    assert (mode(d), mode(f)) == (0o700, 0o600)
    assert f.read_text() == key_line + "\n"


def test_existing_file_keeps_its_lines_and_modes(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str, other_key_line: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    f.write_text(f"# managed by hand\n{other_key_line}\n")
    f.chmod(0o644)
    marks, _ = install(run_sh, shell, home, key_line)
    assert marks == {"PORTKEY_BEGIN", "PORTKEY_ADDED"}
    assert f.read_text() == f"# managed by hand\n{other_key_line}\n{key_line}\n"
    assert (mode(d), mode(f)) == (0o755, 0o644)


def test_file_without_trailing_newline(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str, other_key_line: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    f.write_text(other_key_line)  # no newline at the end
    f.chmod(0o600)
    marks, _ = install(run_sh, shell, home, key_line)
    assert "PORTKEY_ADDED" in marks
    assert f.read_text() == f"{other_key_line}\n{key_line}\n"


def test_empty_existing_file(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    f.write_text("")
    f.chmod(0o600)
    install(run_sh, shell, home, key_line)
    assert f.read_text() == key_line + "\n"


def test_second_run_changes_nothing(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str
) -> None:
    home = home_dir(tmp_path)
    install(run_sh, shell, home, key_line)
    d, f = ssh_files(home)
    before = (f.read_bytes(), f.stat().st_mtime_ns, d.stat().st_mtime_ns, mode(d), mode(f))
    marks, _ = install(run_sh, shell, home, key_line)
    assert marks == {"PORTKEY_BEGIN", "PORTKEY_ALREADY"}
    after = (f.read_bytes(), f.stat().st_mtime_ns, d.stat().st_mtime_ns, mode(d), mode(f))
    assert after == before


@pytest.mark.parametrize(
    "existing",
    [
        "{key}\r\n",  # CRLF, as a file edited on Windows
        'from="10.0.0.0/8",no-agent-forwarding {key}\n',  # with options
        "{type} {blob}\n",  # without the comment
        "{type} {blob} a different comment\n",
        "   {key}\n",  # leading spaces
    ],
)
def test_the_same_key_in_another_form_counts_as_present(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str, existing: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    parsed = parse_key_line(key_line)
    text = existing.format(key=key_line, type=parsed.key_type, blob=parsed.blob)
    f.write_bytes(text.encode())
    f.chmod(0o600)
    marks, _ = install(run_sh, shell, home, key_line)
    assert "PORTKEY_ALREADY" in marks
    assert f.read_bytes() == text.encode()


def test_a_commented_out_key_is_not_present(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    f.write_text(f"# {key_line}\n")
    f.chmod(0o600)
    marks, _ = install(run_sh, shell, home, key_line)
    assert "PORTKEY_ADDED" in marks
    assert f.read_text() == f"# {key_line}\n{key_line}\n"


def test_a_longer_blob_that_starts_the_same_is_another_key(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    parsed = parse_key_line(key_line)
    f.write_text(f"{parsed.key_type} {parsed.blob}AAAA other\n")
    f.chmod(0o600)
    marks, _ = install(run_sh, shell, home, key_line)
    assert "PORTKEY_ADDED" in marks


@pytest.mark.parametrize(
    ("home_mode", "dir_mode", "file_mode"),
    [(0o750, 0o777, 0o600), (0o775, 0o700, 0o600), (0o750, 0o700, 0o622), (0o757, 0o700, 0o600)],
)
def test_loose_permissions_are_reported_and_left_alone(
    run_sh: RunSh,
    shell: str,
    tmp_path: Path,
    key_line: str,
    home_mode: int,
    dir_mode: int,
    file_mode: int,
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir()
    f.write_text("")
    home.chmod(home_mode)
    d.chmod(dir_mode)
    f.chmod(file_mode)
    marks, _ = install(run_sh, shell, home, key_line)
    assert marks == {"PORTKEY_BEGIN", "PORTKEY_ADDED", "PORTKEY_WARN_PERMS"}
    assert (mode(home), mode(d), mode(f)) == (home_mode, dir_mode, file_mode)
    # and the second run still warns, still changes nothing
    marks, _ = install(run_sh, shell, home, key_line)
    assert marks == {"PORTKEY_BEGIN", "PORTKEY_ALREADY", "PORTKEY_WARN_PERMS"}


def test_fix_permissions_script(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir()
    f.write_text(key_line + "\n")
    home.chmod(0o775)
    d.chmod(0o777)
    f.chmod(0o666)
    proc = run_sh(shell, home, FIX_PERMS_SCRIPT)
    assert proc.returncode == 0, proc.stderr
    assert (mode(home), mode(d), mode(f)) == (0o755, 0o755, 0o644)
    marks, _ = install(run_sh, shell, home, key_line)
    assert "PORTKEY_WARN_PERMS" not in marks


@not_root
def test_ssh_dir_not_writable(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    home = home_dir(tmp_path)
    d, _ = ssh_files(home)
    d.mkdir()
    d.chmod(0o500)
    try:
        proc = run_sh(shell, home, build_install_script(key_line))
    finally:
        d.chmod(0o700)
    result = parse_script_output(proc.stdout, proc.stderr, proc.returncode)
    assert result.outcome == "readonly"
    assert "Permission denied" in result.output
    assert list(d.iterdir()) == []


@not_root
def test_authorized_keys_not_writable(
    run_sh: RunSh, shell: str, tmp_path: Path, key_line: str, other_key_line: str
) -> None:
    home = home_dir(tmp_path)
    d, f = ssh_files(home)
    d.mkdir(mode=0o700)
    f.write_text(other_key_line + "\n")
    f.chmod(0o400)
    proc = run_sh(shell, home, build_install_script(key_line))
    assert parse_script_output(proc.stdout, proc.stderr, proc.returncode).outcome == "readonly"
    assert f.read_text() == other_key_line + "\n"


@not_root
def test_home_not_writable(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    home = home_dir(tmp_path)
    home.chmod(0o500)
    try:
        proc = run_sh(shell, home, build_install_script(key_line))
    finally:
        home.chmod(0o750)
    assert parse_script_output(proc.stdout, proc.stderr, proc.returncode).outcome == "readonly"


def test_no_home(run_sh: RunSh, shell: str, tmp_path: Path, key_line: str) -> None:
    proc = run_sh(shell, tmp_path / "missing", build_install_script(key_line))
    result = parse_script_output(proc.stdout, proc.stderr, proc.returncode)
    assert result.outcome == "failed"
    assert "no home directory" in result.output
    assert not (tmp_path / "missing").exists()
