"""asyncssh helpers for tests that talk to the TEST-ONLY sshd containers (package 7).

These connect the way a test wants, not the way Port-Key does: exactly the credentials given,
no agent, no default keys, and the server's expected host key pinned. Port-Key's own client is
package 4's ``portkey.ssh``; use these to set up and check state around it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncssh

from tests.fixtures import testbed as tb
from tests.fixtures.prompter import kbdint_answers

SshConnect = Callable[..., Awaitable[asyncssh.SSHClientConnection]]


class _KbdintClient(asyncssh.SSHClient):
    """Answers keyboard-interactive rounds and records the prompt texts it saw."""

    def __init__(self, answer: Callable[..., list[str]] | None) -> None:
        self._answer = answer
        self._attempted = False
        self.prompts_seen: list[str] = []

    def kbdint_auth_requested(self) -> str | None:
        # One attempt (with as many rounds as the server sends); a failure then ends auth
        # instead of retrying through PAM's failure delay until MaxAuthTries.
        if self._answer is None or self._attempted:
            return None
        self._attempted = True
        return ""

    def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]]
    ) -> list[str] | None:
        assert self._answer is not None
        self.prompts_seen.extend(text for text, _ in prompts)
        return self._answer(prompts=prompts)


def read_fixture_text(raw: bytes) -> str:
    """Decode a fixture file the way Port-Key must: UTF-16 or UTF-8 BOM, CRLF to LF."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return text.replace("\r\n", "\n")


def load_client_key(name: str) -> asyncssh.SSHKey:
    """A fixture client key from ``testbed.CLIENT_KEYS``, decrypted when needed."""
    spec = tb.CLIENT_KEYS[name]
    return asyncssh.import_private_key(read_fixture_text(spec.file.read_bytes()), spec.passphrase)


async def ssh_connect(
    server: tb.SshServer,
    user: str,
    *,
    password: str | None = None,
    key: str | None = None,
    totp: tb.Totp | None = None,
    tunnel: asyncssh.SSHClientConnection | None = None,
    hostkey_set: tb.HostKeySet | None = None,
    connect_timeout: float = 10.0,
    **options: Any,
) -> asyncssh.SSHClientConnection:
    """Connect as ``user`` with exactly the given credentials.

    - ``key``: a fixture client key name (``"id_ed25519"``...).
    - ``password``: password auth; with ``totp`` it answers keyboard-interactive instead
      ("Password: " then a fresh "Verification code: "). The prompts seen are in
      ``conn.get_extra_info("kbdint_prompts")``.
    - ``tunnel``: an open connection to jump through (``sshd`` for ``sshd-inner``).
    - The server must present the host key of ``hostkey_set`` (default: its own set).
    """
    trusted = asyncssh.import_public_key(tb.hostkey_public(hostkey_set or server.hostkey_set))
    answer = kbdint_answers(password or "", totp) if totp else None
    if totp:
        await totp.afresh(min_remaining=4.0)
    methods = [m for m, on in (("publickey", key), ("keyboard-interactive", totp)) if on]
    if password and not totp:
        methods.append("password")
    conn, client = await asyncssh.create_connection(
        lambda: _KbdintClient(answer),
        server.host,
        server.port,
        tunnel=tunnel,
        username=user,
        known_hosts=([trusted], [], []),
        client_keys=[load_client_key(key)] if key else [],
        password=None if totp else password,
        # No credentials at all: offer publickey with no keys, which fails like an empty agent.
        preferred_auth=methods or ["publickey"],
        agent_path=None,
        connect_timeout=connect_timeout,
        **options,
    )
    conn.set_extra_info(kbdint_prompts=client.prompts_seen)
    return conn


async def reset_user(
    user: str,
    *,
    server: tb.SshServer = tb.SSHD,
    totp: tb.Totp | None = None,
    tunnel: asyncssh.SSHClientConnection | None = None,
) -> str:
    """Restore ``user``'s ``~/.ssh`` on ``server`` to the image's initial state (each sshd
    container has its own state; ``sshd-inner`` needs ``tunnel``). Not for ``nosh``."""
    how: dict[str, Any] = {"password": tb.USERS[user].password}
    if user == "keyonly":
        how = {"key": "id_ed25519"}
    elif user == "tfa":
        how["totp"] = totp or tb.Totp()
    async with await ssh_connect(server, user, tunnel=tunnel, **how) as conn:
        result = await conn.run("/usr/local/bin/portkey-test-reset", check=True)
        return str(result.stdout)
