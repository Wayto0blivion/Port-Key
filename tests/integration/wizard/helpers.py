"""Helpers for package 6's integration tests (fixtures are in ``conftest.py``).

- ``start(user, **fields)``: a ``StartMsg`` for ``user`` on sshd, named after the user.
- ``run_ok(svc, msg, prompter, actions)`` / ``run_err(...)``: run ``services.wizard`` with a
  recording ``FakeIO`` (from the unit tests) that answers needs_action with ``actions``.
- ``remote_keys(user, ...)``: ``(authorized_keys text, "<.ssh mode> <file mode>")`` read over
  a separate test connection.
- ``installed_line(svc)``: the line the wizard appends for the default key.
- ``reset(user, server, via)``: put the user's ``~/.ssh`` back (``portkey-test-reset``).
"""

from __future__ import annotations

from typing import Any

from portkey.api.schemas.ws import StartMsg, WizardAction
from portkey.services import Services
from portkey.wizard.remote_script import parse_key_line
from tests.fixtures import testbed as tb
from tests.fixtures.sshclient import ssh_connect
from tests.unit.wizard.fakes import FakeIO


def start(user: str, **fields: Any) -> StartMsg:
    values: dict[str, Any] = {
        "type": "start",
        "host": tb.SSHD.host,
        "port": tb.SSHD.port,
        "username": user,
        "name": f"sshd-{user}",
        "tags": ["test"],
        "color": "green",
    }
    values.update(fields)
    return StartMsg.model_validate(values)


async def run_ok(
    svc: Services, msg: StartMsg, prompter: Any, actions: list[WizardAction] | None = None
) -> tuple[Any, str, FakeIO]:
    io = FakeIO(actions)
    record, outcome = await svc.wizard.run(msg, prompter, io)
    return record, outcome, io


async def run_err(
    svc: Services, msg: StartMsg, prompter: Any, actions: list[WizardAction] | None = None
) -> tuple[BaseException, FakeIO]:
    io = FakeIO(actions)
    try:
        await svc.wizard.run(msg, prompter, io)
    except Exception as exc:
        return exc, io
    raise AssertionError("the wizard run was expected to fail")


async def remote_keys(
    user: str,
    *,
    server: tb.SshServer = tb.SSHD,
    key: str | None = None,
    via: tb.SshServer | None = None,
) -> tuple[str, str]:
    how: dict[str, Any] = {"key": key} if key else {"password": tb.USERS[user].password}

    async def read(conn: Any) -> tuple[str, str]:
        text = await conn.run("cat ~/.ssh/authorized_keys", check=True)
        modes = await conn.run("stat -c %a ~/.ssh ~/.ssh/authorized_keys", check=True)
        return str(text.stdout), " ".join(str(modes.stdout).split())

    if via is not None:
        async with (
            await ssh_connect(via, "keyonly", key="id_ed25519") as jump,
            await ssh_connect(server, user, tunnel=jump, **how) as conn,
        ):
            return await read(conn)
    async with await ssh_connect(server, user, **how) as conn:
        return await read(conn)


def installed_line(svc: Services, key_fp: str | None = None) -> str:
    return parse_key_line(svc.keys.resolve(key_fp).public_key).line


async def reset(user: str, server: tb.SshServer, via: tb.SshServer | None) -> None:
    """Run ``portkey-test-reset`` as ``user`` (through ``via`` when set). tfa and keyonly sign
    in with the fixture key (the wizard installed it for tfa); everyone else with the
    password."""
    how: dict[str, Any] = {"key": "id_ed25519"}
    if user not in ("tfa", "keyonly"):
        how = {"password": tb.USERS[user].password}
    if via is not None:
        async with (
            await ssh_connect(via, "keyonly", key="id_ed25519") as jump,
            await ssh_connect(server, user, tunnel=jump, **how) as conn,
        ):
            await conn.run("/usr/local/bin/portkey-test-reset", check=True)
        return
    async with await ssh_connect(server, user, **how) as conn:
        await conn.run("/usr/local/bin/portkey-test-reset", check=True)
