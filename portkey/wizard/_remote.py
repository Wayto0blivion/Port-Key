"""Running the wizard's commands on a signed-in connection."""

from __future__ import annotations

import logging

import asyncssh

from portkey.models import HopRef
from portkey.ssh.errors import ProtocolError
from portkey.wizard.remote_script import ScriptResult, parse_script_output

log = logging.getLogger("portkey.wizard")

SCRIPT_TIMEOUT = 60.0
VERIFY_COMMAND = "echo portkey-ok"


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


async def run_sh(
    conn: asyncssh.SSHClientConnection, script: str, hop: HopRef, seconds: float
) -> tuple[str, str, int | None]:
    """Run ``sh -s`` with ``script`` on stdin. Returns (stdout, stderr, exit status). A server
    that will not run a command at all gives empty output and no status; a timeout gives what
    arrived so far. A connection that drops raises ``ProtocolError``."""
    try:
        result = await conn.run(
            "sh -s",
            input=script,
            check=False,
            timeout=seconds,
            encoding="utf-8",
            errors="replace",
        )
    except asyncssh.ChannelOpenError:
        log.info("hop %d: the server refused to run a command", hop.index)
        return "", "", None
    except asyncssh.TimeoutError as exc:
        log.info("hop %d: the script did not finish in %.0f s", hop.index, seconds)
        return _text(exc.stdout), _text(exc.stderr), None
    except (asyncssh.Error, OSError) as exc:
        log.info(
            "hop %d: connection lost while running the script (%s)",
            hop.index,
            type(exc).__name__,
        )
        raise ProtocolError(
            f"{hop.name} closed the connection while Port-Key was installing the key.", hop=hop
        ) from None
    return _text(result.stdout), _text(result.stderr), result.exit_status


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
        result = await conn.run(VERIFY_COMMAND, check=False, timeout=SCRIPT_TIMEOUT)
    except (asyncssh.Error, OSError):
        log.info("hop %d: the verify command could not run", hop.index)
        return False
    ok = "portkey-ok" in _text(result.stdout)
    if not ok:
        log.info("hop %d: the verify command printed something else", hop.index)
    return ok
