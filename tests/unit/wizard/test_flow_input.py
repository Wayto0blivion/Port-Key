"""The flow's input checks, reinstalling on a saved connection, and cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from portkey.models import ConnectionDraft, HopTarget
from portkey.ssh.errors import KeyUnreadable, UnknownConnection
from portkey.wizard.errors import InputRejected
from tests.fixtures.prompter import FakePrompter
from tests.unit.wizard.fakes import FakeChain, FakeConn, FakeIO, Timeline, key_works
from tests.unit.wizard.world import World, run, run_fails, start

UNKNOWN_ID = "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a"


def saved(w: World, name: str = "web-prod", **kw: Any) -> Any:
    values: dict[str, Any] = {"name": name, "host": "10.0.0.5", "username": "root"}
    values.update(kw)
    return w.connections.create(ConnectionDraft(auth_method="password", **values))


async def assert_rejected_before_connecting(
    w: World, msg: Any, kind: type[Exception], code: str
) -> None:
    exc, io = await run_fails(w, msg)
    assert isinstance(exc, kind)
    assert getattr(exc, "code", None) == code
    assert io.trail() == [("validate", "running", None), ("validate", "fail", code)]
    assert w.connector.connects == []


async def test_name_taken(world: Callable[..., World]) -> None:
    w = world()
    saved(w, "Web-Prod")
    await assert_rejected_before_connecting(w, start(), InputRejected, "name_taken")


async def test_unknown_connection_id(world: Callable[..., World]) -> None:
    w = world()
    await assert_rejected_before_connecting(
        w, start(connection_id=UNKNOWN_ID), UnknownConnection, "not_found"
    )


async def test_unknown_jump_id(world: Callable[..., World]) -> None:
    w = world()
    await assert_rejected_before_connecting(
        w, start(jump_id=UNKNOWN_ID), UnknownConnection, "not_found"
    )


async def test_jump_loop_on_reinstall(world: Callable[..., World]) -> None:
    w = world()
    record = saved(w)
    msg = start(connection_id=record.id, jump_id=record.id)
    await assert_rejected_before_connecting(w, msg, InputRejected, "jump_cycle")


async def test_unknown_folder(world: Callable[..., World]) -> None:
    w = world()
    await assert_rejected_before_connecting(
        w, start(group_id=UNKNOWN_ID), InputRejected, "group_unknown"
    )


async def test_unknown_key(world: Callable[..., World]) -> None:
    w = world()
    await assert_rejected_before_connecting(
        w, start(key_fp="SHA256:" + "Q" * 43), KeyUnreadable, "key_unreadable"
    )


async def test_no_key_at_all(world: Callable[..., World]) -> None:
    w = world()
    w.keys.records.clear()
    await assert_rejected_before_connecting(w, start(), KeyUnreadable, "key_unreadable")


@pytest.mark.parametrize("public", ["", "ssh-ed25519 not-base64!", "ssh-rsa AAAAC3NzaC1lZDI1NTE5"])
async def test_unreadable_public_half(world: Callable[..., World], public: str) -> None:
    w = world()
    w.keys.records.clear()
    bad = w.keys.add("id_broken", public_key=public)
    exc, _ = await run_fails(w, start(key_fp=bad.fp))
    assert isinstance(exc, KeyUnreadable)
    assert "public half of id_broken" in exc.message


async def test_reinstall_updates_the_saved_connection(world: Callable[..., World]) -> None:
    w = world(key_works())
    record = saved(w, "old-name")
    new, outcome, _, _ = await run(w, start(connection_id=record.id, key_fp=w.key.fp))
    assert outcome == "already_installed"
    assert new.id == record.id
    after = w.connections.get(record.id)
    assert (after.name, after.host, after.username, after.auth_method, after.key_fp) == (
        "web-prod",
        "192.168.10.42",
        "john",
        "key",
        w.key.fp,
    )
    assert len(w.connections.list_all()) == 1
    assert w.connector.connects[0][-1].ref.connection_id == record.id


async def test_save_errors_become_input_rejected(world: Callable[..., World]) -> None:
    w = world(key_works())

    async def grab_name(hops: list[HopTarget], prompter: Any) -> FakeChain:
        saved(w, "web-prod")  # someone saved the same name meanwhile
        return await key_works()(hops, prompter)

    w.connector.attempts = [grab_name]
    exc, io = await run_fails(w)
    assert isinstance(exc, InputRejected)
    assert exc.code == "name_taken"
    assert io.trail()[-1] == ("save", "fail", "name_taken")


async def test_cancelling_the_run_closes_the_password_connection(
    world: Callable[..., World],
) -> None:
    chain_holder: list[FakeChain] = []
    blocked = asyncio.Event()

    class Blocking(FakeConn):
        async def run(self, command: str, **kw: Any) -> Any:
            blocked.set()
            await asyncio.Event().wait()

    async def signed_in(hops: list[HopTarget], prompter: Any) -> FakeChain:
        target = hops[-1].ref
        await prompter.status("auth", target, method="password")
        await prompter.status("ready", target)
        chain = FakeChain(Blocking(), hops, Timeline())
        chain_holder.append(chain)
        return chain

    w = world(signed_in)
    task = asyncio.create_task(w.wizard.run(start(), FakePrompter(), FakeIO()))
    await asyncio.wait_for(blocked.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert chain_holder[0].closed
    assert w.connections.list_all() == []
