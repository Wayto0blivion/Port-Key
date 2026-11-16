"""The flow's failures: each one marks the step where it happened, then ends the run; and the
same flow through a jump host."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from portkey.models import ConnectionDraft
from portkey.ssh.errors import (
    AuthFailed,
    ConnectionRefused,
    HostKeyChanged,
    HostKeyRejected,
    JumpFailed,
    PasswordExpired,
)
from tests.unit.wizard.fakes import jump_target, key_works, password_works, raises
from tests.unit.wizard.world import World, run, run_fails, start


async def test_wrong_passwords_end_with_auth_failed(world: Callable[..., World]) -> None:
    async def three_wrong(hops: list[Any], prompter: Any) -> Any:
        target = hops[-1]
        await prompter.status("connecting", target.ref)
        await prompter.status("hostkey", target.ref)
        await prompter.status("auth", target.ref, method="publickey")
        await prompter.status("auth", target.ref, method="password")
        raise AuthFailed("rejected", methods=["publickey", "password"], hop=target.ref)

    w = world(three_wrong)
    exc, io = await run_fails(w)
    assert isinstance(exc, AuthFailed)
    assert io.trail()[-1] == ("password_login", "fail", "auth_failed")
    assert w.connections.list_all() == []


@pytest.mark.parametrize(
    ("exc", "failed_step"),
    [
        (ConnectionRefused("refused"), ("connect", "fail", "refused")),
        (PasswordExpired("expired"), ("connect", "fail", "password_expired")),
    ],
)
async def test_network_failures_fail_the_running_step(
    world: Callable[..., World], exc: Any, failed_step: tuple[str, str, str]
) -> None:
    w = world(raises(exc))
    raised, io = await run_fails(w)
    assert raised is exc
    assert io.trail()[-1] == failed_step
    assert io.steps[-1].hop is not None


async def test_host_key_changed_fails_the_hostkey_step(world: Callable[..., World]) -> None:
    async def changed(hops: list[Any], prompter: Any) -> Any:
        target = hops[-1].ref
        await prompter.status("connecting", target)
        await prompter.status("hostkey", target)
        raise HostKeyChanged("changed", hop=target)

    w = world(changed)
    exc, io = await run_fails(w)
    assert isinstance(exc, HostKeyChanged)
    assert io.trail()[-1] == ("hostkey", "fail", "hostkey_changed")


async def test_declining_a_host_key_is_not_a_failure(world: Callable[..., World]) -> None:
    w = world(raises(HostKeyRejected("no")))
    exc, io = await run_fails(w)
    assert isinstance(exc, HostKeyRejected)
    assert all(state != "fail" for _, state, _ in io.trail())


def add_jump(w: World) -> str:
    w.connector.jumps = [jump_target()]
    jump = w.connections.create(
        ConnectionDraft(
            name="build-box", host="build-box.test", username="john", auth_method="key"
        )
    )
    return jump.id


async def test_through_a_jump_host(world: Callable[..., World]) -> None:
    w = world(password_works(), key_works())
    jump_id = add_jump(w)
    record, outcome, io, _ = await run(w, start(jump_id=jump_id))
    assert outcome == "installed"
    connect = [(s.state, s.hop.index if s.hop else None) for s in io.steps if s.step == "connect"]
    assert connect == [("running", 0), ("ok", 0), ("running", 1), ("ok", 1)]
    assert [len(h) for h in w.connector.connects] == [2, 2]
    assert w.connections.get(record.id).jump_id == jump_id


async def test_a_failing_jump_reports_on_its_hop(world: Callable[..., World]) -> None:
    w = world(raises(JumpFailed(ConnectionRefused("refused")), on="jump"))
    jump_id = add_jump(w)
    exc, io = await run_fails(w, start(jump_id=jump_id))
    assert isinstance(exc, JumpFailed)
    assert io.steps[-1].step == "connect"
    assert io.steps[-1].state == "fail"
    assert io.steps[-1].hop is not None
    assert io.steps[-1].hop.role == "jump"
