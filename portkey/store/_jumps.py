"""Jump-chain rules (docs/architecture.md section 5, "Jump host rules").

A connection's ``jump_id`` names the connection it is reached through. A chain has at most
``max_hops`` jump hosts before the target and no cycles. Changing one connection's jump can
lengthen the chains of every connection that jumps through it, so the depth check adds the
height of the dependants below the connection being changed.

Functions take the ``sqlite3.Connection`` of the caller's transaction so the check and the write
see the same data.
"""

from __future__ import annotations

import sqlite3

from portkey.errors import ErrorCode, Invalid

CYCLE_MESSAGE = (
    "A connection can't jump through itself, or through a host that already jumps through it."
)
UNKNOWN_MESSAGE = "That jump host doesn't exist any more. Choose another one."


def too_deep_message(max_hops: int) -> str:
    return f"Jump chains can be at most {max_hops} hosts long."


def jump_map(c: sqlite3.Connection) -> dict[str, str | None]:
    """Every connection id mapped to its jump id."""
    return {row[0]: row[1] for row in c.execute("SELECT id, jump_id FROM connections")}


def _cycle() -> Invalid:
    return Invalid(CYCLE_MESSAGE, code=ErrorCode.JUMP_CYCLE)


def chain_up(jumps: dict[str, str | None], start: str) -> list[str]:
    """``start`` and every jump above it, nearest first. Raises ``Invalid(jump_cycle)`` on a
    loop and ``Invalid(jump_unknown)`` on a dangling id."""
    out: list[str] = []
    seen: set[str] = set()
    cur: str | None = start
    while cur is not None:
        if cur in seen:
            raise _cycle()
        if cur not in jumps:
            raise Invalid(UNKNOWN_MESSAGE, code=ErrorCode.JUMP_UNKNOWN)
        seen.add(cur)
        out.append(cur)
        cur = jumps[cur]
    return out


def dependant_height(jumps: dict[str, str | None], conn_id: str) -> int:
    """How many levels of connections jump through ``conn_id`` (0 when none do)."""
    children: dict[str, list[str]] = {}
    for cid, jid in jumps.items():
        if jid is not None:
            children.setdefault(jid, []).append(cid)
    height = 0
    frontier = [conn_id]
    seen = {conn_id}
    while True:
        nxt = [k for p in frontier for k in children.get(p, ()) if k not in seen]
        if not nxt:
            return height
        seen.update(nxt)
        height += 1
        frontier = nxt


def validate_jump(
    c: sqlite3.Connection, conn_id: str | None, jump_id: str | None, max_hops: int
) -> None:
    """Raise ``Invalid(jump_unknown|jump_cycle|jump_too_deep)`` unless ``conn_id`` (None for a
    new connection) may use ``jump_id``."""
    if jump_id is None:
        return
    jumps = jump_map(c)
    if jump_id not in jumps:
        raise Invalid(UNKNOWN_MESSAGE, code=ErrorCode.JUMP_UNKNOWN)
    if conn_id is not None and jump_id == conn_id:
        raise _cycle()
    above = chain_up(jumps, jump_id)
    if conn_id is not None and conn_id in above:
        raise _cycle()
    below = dependant_height(jumps, conn_id) if conn_id is not None else 0
    if len(above) + below > max_hops:
        raise Invalid(too_deep_message(max_hops), code=ErrorCode.JUMP_TOO_DEEP)
