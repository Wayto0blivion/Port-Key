"""Running the wizard's commands on a signed-in connection.

Output is read as it arrives and kept only up to ``OUTPUT_CAP`` characters per stream: past the
cap the channel is closed, as for a timeout. A compromised or impersonated server answering with
endless output can therefore not fill the container's memory (asyncssh's ``conn.run`` would keep
all of it until the 60-second timeout). The wizard needs only the markers and the first few KB
for the Details block (``remote_script.clip_output`` keeps 4 KB).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import asyncssh

from portkey.models import HopRef
from portkey.ssh.errors import ProtocolError
from portkey.wizard.remote_script import ScriptResult, parse_script_output

log = logging.getLogger("portkey.wizard")

SCRIPT_TIMEOUT = 60.0
VERIFY_COMMAND = "echo portkey-ok"
OUTPUT_CAP = 64 * 1024  # characters kept per stream
_READ = 4096


@dataclass
class _Output:
    stdout: str
    stderr: str
    status: int | None  # None: no exit status (timed out, too much output, nothing ran)
    cut: str | None = None  # "timeout" or "too_much" when the command was stopped


class _TooMuch(Exception):
    """A stream passed ``OUTPUT_CAP``."""


async def _pump(reader: asyncssh.SSHReader[str], kept: list[str], cap: int) -> None:
    size = 0
    while True:
        chunk = await reader.read(_READ)
        if not chunk:
            return
        if size < cap:
            kept.append(chunk[: cap - size])
        size += len(chunk)
        if size > cap:
            raise _TooMuch


async def run_capped(
    conn: asyncssh.SSHClientConnection,
    command: str,
    stdin: str | None,
    seconds: float,
    cap: int = OUTPUT_CAP,
) -> _Output:
    """Run ``command`` with ``stdin`` (then end of input), reading at most ``cap`` characters
    of each stream within ``seconds``. Raises ``asyncssh.ChannelOpenError`` when the server
    will not run a command, and ``asyncssh.Error``/``OSError`` when the connection drops."""
    proc = await conn.create_process(command, encoding="utf-8", errors="replace")
    out: list[str] = []
    err: list[str] = []
    cut: str | None = None
    status: int | None = None
    try:
        async with asyncio.timeout(seconds):
            if stdin is not None:
                proc.stdin.write(stdin)
            proc.stdin.write_eof()
            await asyncio.gather(_pump(proc.stdout, out, cap), _pump(proc.stderr, err, cap))
            await proc.wait_closed()
            status = proc.exit_status
    except TimeoutError:
        cut = "timeout"
    except _TooMuch:
        cut = "too_much"
    finally:
        if status is None:
            with contextlib.suppress(Exception):
                proc.close()
    return _Output("".join(out), "".join(err), status, cut)


async def run_sh(
    conn: asyncssh.SSHClientConnection, script: str, hop: HopRef, seconds: float
) -> tuple[str, str, int | None]:
    """Run ``sh -s`` with ``script`` on stdin. Returns (stdout, stderr, exit status). A server
    that will not run a command at all gives empty output and no status; a timeout or too much
    output gives what arrived so far and no status. A connection that drops raises
    ``ProtocolError``."""
    try:
        result = await run_capped(conn, "sh -s", script, seconds)
    except asyncssh.ChannelOpenError:
        log.info("hop %d: the server refused to run a command", hop.index)
        return "", "", None
    except (asyncssh.Error, OSError) as exc:
        log.info(
            "hop %d: connection lost while running the script (%s)",
            hop.index,
            type(exc).__name__,
        )
        raise ProtocolError(
            f"{hop.name} closed the connection while Port-Key was installing the key.", hop=hop
        ) from None
    if result.cut == "timeout":
        log.info("hop %d: the script did not finish in %.0f s", hop.index, seconds)
    elif result.cut == "too_much":
        log.info("hop %d: the script printed over %d characters; stopped", hop.index, OUTPUT_CAP)
    return result.stdout, result.stderr, result.status


async def run_install(
    conn: asyncssh.SSHClientConnection, script: str, hop: HopRef, seconds: float = SCRIPT_TIMEOUT
) -> ScriptResult:
    stdout, stderr, status = await run_sh(conn, script, hop, seconds)
    result = parse_script_output(stdout, stderr, status)
    log.info(
        "hop %d: install script %s (exit %s, perms warning %s)",
        hop.index,
        result.outcome,
        status,
        result.perms_warning,
    )
    return result


async def run_check(conn: asyncssh.SSHClientConnection, hop: HopRef) -> bool:
    """``echo portkey-ok`` on the verify connection; True when it printed that. Signing in is
    what proves the key works, so a failure here is only logged."""
    try:
        result = await run_capped(conn, VERIFY_COMMAND, None, SCRIPT_TIMEOUT)
    except (asyncssh.Error, OSError):
        log.info("hop %d: the verify command could not run", hop.index)
        return False
    ok = "portkey-ok" in result.stdout
    if not ok:
        log.info("hop %d: the verify command printed something else", hop.index)
    return ok
