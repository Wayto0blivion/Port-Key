"""Diagnostics routes (package 7): /api/diag*. Contract: docs/api.md "Diagnostics".

``GET /api/diag`` reports mounts, writability, key problems and versions (never key material).
``POST /api/diag/resolve`` resolves a name from inside the container (``getaddrinfo``) and tries
a TCP connect to the port with ``limits.reachability_timeout``.

The resolve check must not become a scanner that a web page can drive: it sits behind the
bearer token and the Host/Origin guard like every other mutating route, takes exactly one host
and port, tries at most ``MAX_TCP_ADDRESSES`` addresses within one short deadline, sends nothing
and closes the socket as soon as it connects.

Writability of the generated-keys and data folders is probed with an anonymous temporary file
(``O_TMPFILE`` where the filesystem has it), because on a Windows bind mount the mode bits say
0777 whatever the folder's ACLs allow. The ``.ssh`` mount is never written to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import socket
import stat
import tempfile
import time
from pathlib import Path
from typing import Literal

import asyncssh
from fastapi import APIRouter, Depends

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import (
    DiagReport,
    DirStatus,
    KeyProblemDiag,
    ResolveRequest,
    ResolveResult,
    SshDirStatus,
    TcpCheck,
)
from portkey.interfaces import KeyProvider
from portkey.models import KeyProblem, KeyRecord

log = logging.getLogger("portkey.diag")

router = APIRouter(prefix="/api/diag", tags=["diag"], dependencies=[Depends(require_auth)])

#: Name resolution gives up after this many seconds (the resolver itself may wait longer).
RESOLVE_TIMEOUT = 3.0
#: At most this many resolved addresses are tried, all within one reachability deadline.
MAX_TCP_ADDRESSES = 4

TcpError = Literal["refused", "timeout", "unreachable"]

MODE_NOTE = "Files in the mounted folder show as mode 0777. Port-Key does not need stricter modes."
AGENT_NOTE = (
    "If your key lives only in the Windows ssh-agent, Port-Key can't reach it. Generate a key "
    "here and install it with the wizard."
)
PROBLEM_DETAIL = {
    "ppk": "PuTTY format, not supported",
    "not_private_key": "Not a private key",
    "unreadable": "Unreadable",
    "unsupported_type": "Key type not supported",
}


# --- GET /api/diag --------------------------------------------------------------------------------


@router.get("", response_model=DiagReport)
def diagnostics(services: ServicesDep) -> DiagReport:
    config = services.config
    notes: list[str] = []
    keys, problems, scan_ok = _scan_keys(services.keys, notes)
    ssh_keys = [k for k in keys if k.source == "ssh_dir"]
    ssh = config.ssh_dir
    ssh_status = SshDirStatus(
        **_dir_status(ssh, config.ssh_dir_display, probe_write=False).model_dump(),
        key_count=len(ssh_keys),
        known_hosts=config.known_hosts_path.is_file(),
        mode_note=MODE_NOTE if _shows_0777(ssh) else None,
    )
    if not ssh_status.present:
        notes.append(
            f"The .ssh folder ({config.ssh_dir_display}) is not mounted. Check the volumes in "
            "compose.yaml."
        )
    elif scan_ok and not keys:
        notes.append(AGENT_NOTE)
    return DiagReport(
        version=config.version,
        python=platform.python_version(),
        asyncssh=str(getattr(asyncssh, "__version__", "unknown")),
        ssh_dir=ssh_status,
        keys_dir=_dir_status(config.keys_dir, config.keys_dir_display, probe_write=True),
        data_dir=_dir_status(config.data_dir, str(config.data_dir), probe_write=True),
        key_problems=[_problem(p) for p in problems],
        notes=notes,
    )


def _scan_keys(
    provider: KeyProvider, notes: list[str]
) -> tuple[list[KeyRecord], list[KeyProblem], bool]:
    """Keys and problems from the keys package. A failing scan is reported, never raised: the
    diagnostics page is what the owner opens when something is broken."""
    try:
        return provider.list_keys(), provider.problems(), True
    except Exception as exc:  # any failure belongs in the report, not in a 500
        log.warning("diagnostics: key scan failed (%s)", type(exc).__name__)
        notes.append(f"Port-Key could not scan for keys ({type(exc).__name__}).")
        return [], [], False


def _problem(problem: KeyProblem) -> KeyProblemDiag:
    return KeyProblemDiag(
        path=problem.display_path,
        reason=problem.reason,
        detail=problem.detail or PROBLEM_DETAIL.get(problem.reason, ""),
    )


def _dir_status(path: Path, display: str, *, probe_write: bool) -> DirStatus:
    present = path.is_dir()
    readable = present and os.access(path, os.R_OK | os.X_OK) and _can_list(path)
    if not present:
        writable = False
    elif probe_write:
        writable = _can_create_file(path)
    else:
        writable = os.access(path, os.W_OK)
    return DirStatus(path=display, present=present, readable=readable, writable=writable)


def _can_list(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError:
        return False
    return True


def _can_create_file(path: Path) -> bool:
    """Create an anonymous temp file in ``path`` (nothing is left behind)."""
    try:
        with tempfile.TemporaryFile(dir=path, prefix=".portkey-write-check-"):
            return True
    except OSError:
        return False


def _shows_0777(path: Path) -> bool:
    """True when the folder or any file directly in it has mode 0777 (a Windows bind mount)."""
    try:
        if stat.S_IMODE(path.stat().st_mode) == 0o777:
            return True
        with os.scandir(path) as entries:
            return any(
                stat.S_IMODE(e.stat(follow_symlinks=False).st_mode) == 0o777 for e in entries
            )
    except OSError:
        return False


# --- POST /api/diag/resolve ---------------------------------------------------------------------


@router.post("/resolve", response_model=ResolveResult)
async def resolve(body: ResolveRequest, services: ServicesDep) -> ResolveResult:
    host = body.host.strip("[]")  # tolerate "[::1]"; the schema already forbids spaces, @ and /
    addresses, error = await resolve_host(host, body.port)
    if error is not None:
        tcp = TcpCheck(state="skipped", ms=None, error=None)
    else:
        tcp = await tcp_check(addresses, body.port, services.config.limits.reachability_timeout)
    log.info(
        "diag resolve %s:%d -> %d address(es), tcp %s", host, body.port, len(addresses), tcp.state
    )
    return ResolveResult(
        host=body.host, port=body.port, addresses=addresses, resolve_error=error, tcp=tcp
    )


async def resolve_host(
    host: str, port: int, limit: float = RESOLVE_TIMEOUT
) -> tuple[list[str], str | None]:
    """``(addresses, None)`` in resolver order without duplicates, or ``([], message)``.
    Gives up after ``limit`` seconds."""
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(limit):
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return [], exc.strerror or str(exc)
    except TimeoutError:
        return [], f"No answer from the resolver within {limit:g} seconds"
    except (OSError, UnicodeError) as exc:
        return [], str(exc) or type(exc).__name__
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        return [], "The name has no addresses"
    return addresses, None


async def tcp_check(addresses: list[str], port: int, limit: float) -> TcpCheck:
    """Connect to the addresses in order until one answers, all within ``limit`` seconds."""
    if not addresses:
        return TcpCheck(state="skipped", ms=None, error=None)
    deadline = time.monotonic() + limit
    error: TcpError = "timeout"
    for address in addresses[:MAX_TCP_ADDRESSES]:
        left = deadline - time.monotonic()
        if left <= 0:
            error = "timeout"
            break
        started = time.monotonic()
        try:
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), left)
        except TimeoutError:
            error = "timeout"
            break
        except ConnectionRefusedError:
            error = "refused"
            continue
        except OSError:
            error = "unreachable"
            continue
        ms = round((time.monotonic() - started) * 1000)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return TcpCheck(state="up", ms=ms, error=None)
    return TcpCheck(state="down", ms=None, error=error)
