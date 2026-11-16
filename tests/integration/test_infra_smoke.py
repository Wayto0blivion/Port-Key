"""Smoke tests for the integration test bed itself (package 7).

If these fail, every other integration test is suspect: the sshd users, host key sets, network
layout, fixture files and helpers in ``conftest.py`` are what packages 4, 5 and 6 build on.
"""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncssh
import pytest

from portkey.models import HopRef
from portkey.ssh.errors import PromptCancelled
from tests.fixtures import testbed as tb
from tests.fixtures.prompter import CANCEL, FakePrompter, UnexpectedPrompt, kbdint_answers
from tests.fixtures.sshclient import SshConnect, load_client_key

HOP = HopRef(0, 1, None, "sshd", "sshd", 22, "target")


async def whoami(conn: asyncssh.SSHClientConnection) -> str:
    result = await conn.run("whoami", check=True)
    return str(result.stdout).strip()


# --- fixture files -----------------------------------------------------------------------------


def test_hostkey_sets_differ_and_match_their_files() -> None:
    fp_a, fp_b = tb.hostkey_fingerprint("a"), tb.hostkey_fingerprint("b")
    assert fp_a != fp_b
    for key_set, fp in (("a", fp_a), ("b", fp_b)):
        key = asyncssh.read_private_key(tb.hostkey_path(key_set))  # type: ignore[arg-type]
        assert key.get_fingerprint() == fp
        assert "TEST-ONLY" in tb.hostkey_path(key_set).with_suffix(".pub").read_text()


@pytest.mark.parametrize("name", sorted(tb.CLIENT_KEYS))
def test_client_keys_load_and_match_the_seed(name: str) -> None:
    spec = tb.CLIENT_KEYS[name]
    key = load_client_key(name)
    assert key.get_fingerprint() == tb.fingerprint(tb.client_public(name))
    assert "TEST-ONLY" in spec.comment
    assert (spec.file.with_name(spec.file.name + ".pub")).exists() == spec.pub_file
    if spec.encrypted:
        with pytest.raises(asyncssh.KeyImportError):
            asyncssh.import_private_key(spec.file.read_text())


def test_fixture_encodings_are_what_the_names_say() -> None:
    ssh = tb.SSH_FIXTURE_DIR
    assert b"\r\n" in (ssh / "known_hosts").read_bytes()
    assert b"\r\n" in (ssh / "id_rsa").read_bytes()
    assert (ssh / "id_rsa.pub").read_bytes().startswith(b"\xff\xfe")
    assert (ssh / "id_ecdsa").read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r" not in (ssh / "id_ed25519").read_bytes()
    assert set(tb.PROBLEM_FILES) <= {p.name for p in ssh.iterdir()}


def test_known_hosts_has_plain_hashed_and_port_entries() -> None:
    text = (tb.SSH_FIXTURE_DIR / "known_hosts").read_bytes().decode().replace("\r\n", "\n")
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    key_a = tb.hostkey_public("a")
    assert f"sshd {key_a}" in lines
    assert f"[sshd]:2222 {key_a}" in lines
    assert any(ln.startswith("|1|") and ln.endswith(key_a) for ln in lines)
    assert not any("sshd-rekeyed" in ln for ln in lines)
    known = asyncssh.import_known_hosts(text)
    for host, port in (("sshd", 22), ("sshd", 2222), ("sshd-inner", 22)):
        trusted = known.match(host, "", port)[0]
        assert [k.export_public_key().decode().split()[1] for k in trusted] == [key_a.split()[1]]
    # Note: asyncssh's match() falls back to the port-less entry when no [host]:port entry
    # matches (OpenSSH does not), so "sshd" port 2200 would match "sshd" here.


def test_fixture_ssh_dir_copy_is_0777(fixture_ssh_dir: Path) -> None:
    for path in [fixture_ssh_dir, *fixture_ssh_dir.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o777, path


# --- helpers -------------------------------------------------------------------------------------


async def test_fake_prompter_scripts_answers_in_order() -> None:
    p = FakePrompter(hostkey_new=True, password=["wrong", "right"], passphrase=("pp", True))
    assert await p.hostkey_new(HOP, "ssh-ed25519", "SHA256:x") is True
    assert await p.password(HOP, "john", False) == "wrong"
    assert await p.password(HOP, "john", True) == "right"
    with pytest.raises(UnexpectedPrompt):
        await p.password(HOP, "john", True)
    with pytest.raises(UnexpectedPrompt):
        await p.kbdint(HOP, "", "", [("Code: ", False)])
    assert p.kinds() == ["hostkey_new", "password", "password", "password", "kbdint"]
    assert [c.detail["retry"] for c in p.calls[1:3]] == [False, True]
    await p.status("connecting", HOP)
    assert p.phases() == [(0, "connecting")]


async def test_fake_prompter_cancel_and_kbdint_answers(totp: tb.Totp) -> None:
    p = FakePrompter(hostkey_new=CANCEL, kbdint=kbdint_answers("pw", totp))
    with pytest.raises(PromptCancelled):
        await p.hostkey_new(HOP, "ssh-ed25519", "SHA256:x")
    answers = await p.kbdint(HOP, "", "", [("Password: ", False), ("Verification code: ", False)])
    assert answers[0] == "pw"
    assert answers[1].isdigit()
    assert len(answers[1]) == 6


# --- live sshd -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("server", "key_set"),
    [(tb.SSHD, "a"), (tb.SSHD_ALT_PORT, "a"), (tb.SSHD_REKEYED, "b")],
    ids=["sshd", "sshd-2222", "sshd-rekeyed"],
)
async def test_servers_present_their_host_key_set(server: tb.SshServer, key_set: str) -> None:
    key = await asyncssh.get_server_host_key(server.host, server.port)
    assert key is not None
    assert key.get_fingerprint() == tb.hostkey_fingerprint(key_set)  # type: ignore[arg-type]


async def test_pw_signs_in_with_password_only(ssh_connect: SshConnect) -> None:
    user = tb.USERS["pw"]
    async with await ssh_connect(tb.SSHD, "pw", password=user.password) as conn:
        assert await whoami(conn) == "pw"
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "pw", key="id_ed25519")
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "pw", password="TEST-ONLY wrong password")


@pytest.mark.parametrize("key", [k for k in tb.CLIENT_KEYS if k != "id_ed25519_portkey"])
async def test_keyonly_signs_in_with_each_seeded_key(ssh_connect: SshConnect, key: str) -> None:
    async with await ssh_connect(tb.SSHD, "keyonly", key=key) as conn:
        assert await whoami(conn) == "keyonly"


async def test_keyonly_refuses_passwords_and_unknown_keys(ssh_connect: SshConnect) -> None:
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "keyonly", password=tb.USERS["keyonly"].password)
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "keyonly", key="id_ed25519_portkey")


async def test_fresh_has_no_ssh_dir(ssh_connect: SshConnect) -> None:
    async with await ssh_connect(tb.SSHD, "fresh", password=tb.USERS["fresh"].password) as conn:
        assert (await conn.run("test -e ~/.ssh")).exit_status == 1


async def test_tfa_needs_password_and_totp(ssh_connect: SshConnect, totp: tb.Totp) -> None:
    user = tb.USERS["tfa"]
    async with await ssh_connect(tb.SSHD, "tfa", password=user.password, totp=totp) as conn:
        assert await whoami(conn) == "tfa"
        assert tuple(conn.get_extra_info("kbdint_prompts")) == tb.TFA_PROMPTS
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "tfa", password=user.password)


async def test_tfa_rejects_a_wrong_code(ssh_connect: SshConnect) -> None:
    class WrongCode(tb.Totp):
        def now(self) -> str:
            return "000000" if super().now() != "000000" else "111111"

    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_connect(tb.SSHD, "tfa", password=tb.USERS["tfa"].password, totp=WrongCode())


async def test_fishy_gets_a_fish_login_shell(ssh_connect: SshConnect) -> None:
    async with await ssh_connect(tb.SSHD, "fishy", password=tb.USERS["fishy"].password) as conn:
        result = await conn.run("echo $FISH_VERSION", check=True)
        assert str(result.stdout).strip()


async def test_badperms_strictmodes_refuses_its_authorized_keys(
    ssh_connect: SshConnect, reset_ssh_user: Callable[[str], Awaitable[str]]
) -> None:
    password = tb.USERS["badperms"].password
    line = f"{tb.client_public('id_ed25519')} TEST-ONLY"
    async with await ssh_connect(tb.SSHD, "badperms", password=password) as conn:
        assert (await conn.run("stat -c %a ~/.ssh", check=True)).stdout.strip() == "777"
        await conn.run(f"printf '%s\\n' '{line}' > ~/.ssh/authorized_keys", check=True)
    try:
        with pytest.raises(asyncssh.PermissionDenied):
            await ssh_connect(tb.SSHD, "badperms", key="id_ed25519")
    finally:
        await reset_ssh_user("badperms")


async def test_nosh_signs_in_but_cannot_run_anything(ssh_connect: SshConnect) -> None:
    async with await ssh_connect(tb.SSHD, "nosh", password=tb.USERS["nosh"].password) as conn:
        result = await conn.run("echo hi")
        assert result.exit_status == 1
        assert tb.NOLOGIN_MESSAGE in str(result.stdout)


async def test_reset_restores_a_user(
    ssh_connect: SshConnect, reset_ssh_user: Callable[[str], Awaitable[str]]
) -> None:
    password = tb.USERS["fresh"].password
    async with await ssh_connect(tb.SSHD, "fresh", password=password) as conn:
        await conn.run("mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys", check=True)
    assert "fresh" in await reset_ssh_user("fresh")
    async with await ssh_connect(tb.SSHD, "fresh", password=password) as conn:
        assert (await conn.run("test -e ~/.ssh")).exit_status == 1


async def test_sshd_inner_is_not_reachable_directly() -> None:
    with pytest.raises((OSError, asyncio.TimeoutError)):
        await asyncio.wait_for(asyncio.open_connection(tb.SSHD_INNER.host, 22), timeout=5)


async def test_sshd_inner_is_reachable_through_sshd(ssh_connect: SshConnect) -> None:
    async with await ssh_connect(tb.SSHD, "keyonly", key="id_ed25519") as jump:
        outer_name = (await jump.run("hostname", check=True)).stdout
        key = await asyncssh.get_server_host_key(tb.SSHD_INNER.host, 22, tunnel=jump)
        assert key is not None
        assert key.get_fingerprint() == tb.hostkey_fingerprint("a")
        async with await ssh_connect(tb.SSHD_INNER, "keyonly", key="id_ed25519", tunnel=jump) as c:
            assert await whoami(c) == "keyonly"
            assert (await c.run("hostname", check=True)).stdout != outer_name
