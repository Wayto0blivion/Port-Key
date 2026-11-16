"""wizard._remote: output is read with a cap, so a hostile server cannot fill memory."""

from __future__ import annotations

import asyncio
from typing import Any

from portkey.models import HopRef
from portkey.wizard import _remote
from portkey.wizard.remote_script import MARK_BEGIN
from tests.unit.wizard.fakes import FakeConn, FakeProcess

HOP = HopRef(0, 1, None, "web-prod", "192.168.10.42", 22, "target")


class _Endless:
    """A stream that never ends: ``head`` first, then the same text forever."""

    def __init__(self, head: str = "") -> None:
        self.head = head
        self.served = 0

    async def read(self, n: int = -1) -> str:
        await asyncio.sleep(0)
        if self.head:
            chunk, self.head = self.head, ""
        else:
            chunk = "y\n" * (max(n, 2) // 2)
        self.served += len(chunk)
        return chunk


class _Silent:
    async def read(self, n: int = -1) -> str:
        await asyncio.Event().wait()
        return ""


class _Flood(FakeConn):
    def __init__(self, stdout: Any) -> None:
        super().__init__()
        self.stream = stdout

    async def create_process(self, command: str, **kw: Any) -> FakeProcess:
        process = await super().create_process(command, **kw)
        process._eof = lambda: None  # type: ignore[method-assign]
        process.stdout = self.stream
        return process


async def test_endless_output_is_cut_at_the_cap_and_the_channel_closed() -> None:
    # Security review: conn.run kept every byte until the 60 s timeout (gigabytes at LAN speed).
    flood = _Endless()
    conn = _Flood(flood)
    stdout, stderr, status = await asyncio.wait_for(_remote.run_sh(conn, "", HOP, 60.0), 5)  # type: ignore[arg-type]
    assert len(stdout) == _remote.OUTPUT_CAP
    assert (stderr, status) == ("", None)
    assert flood.served <= _remote.OUTPUT_CAP + 2 * _remote._READ  # stopped right after the cap
    assert conn.processes[0].closed


async def test_flooding_before_the_script_starts_reads_as_non_posix() -> None:
    result = await _remote.run_install(_Flood(_Endless()), "script", HOP, 60.0)  # type: ignore[arg-type]
    assert result.outcome == "non_posix"


async def test_flooding_after_the_script_started_reads_as_failed() -> None:
    conn = _Flood(_Endless(head=f"{MARK_BEGIN}\n"))
    result = await _remote.run_install(conn, "script", HOP, 60.0)  # type: ignore[arg-type]
    assert result.outcome == "failed"


async def test_a_command_that_never_finishes_times_out_with_what_arrived() -> None:
    conn = _Flood(_Silent())
    stdout, _, status = await _remote.run_sh(conn, "", HOP, 0.05)  # type: ignore[arg-type]
    assert (stdout, status) == ("", None)
    assert conn.processes[0].closed


async def test_normal_output_and_status_come_back_whole() -> None:
    conn = FakeConn(lambda command, stdin: ("out\n", "err\n", 3))
    assert await _remote.run_sh(conn, "echo", HOP, 5.0) == ("out\n", "err\n", 3)  # type: ignore[arg-type]
    assert conn.commands == [("sh -s", "echo")]
    assert not conn.processes[0].closed


async def test_the_verify_command_under_a_flood_still_answers() -> None:
    assert await _remote.run_check(_Flood(_Endless(head="portkey-ok\n")), HOP) is True  # type: ignore[arg-type]
