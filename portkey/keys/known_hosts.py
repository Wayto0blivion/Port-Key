"""Read-only known_hosts parsing (package 3). Implements ``interfaces.KnownHostsReader``.

Reads ``/ssh/known_hosts`` through ``textio.read_text_normalized`` and re-reads it when the
mtime changes. Handles plain and hashed (``|1|``) names, ``[host]:port``, ``@cert-authority`` and
``@revoked`` markers, and wildcards (matched, never suggested). Suggestions merge entries that
share a key; the preferred ``host`` is a name before an IP address; hashed entries are only
counted. The owner's file is never written.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
from pathlib import Path

from portkey.keys._kh_parse import (
    Entry,
    entry_matches,
    lookup_name,
    parse_known_hosts,
    split_plain,
)
from portkey.keys._wire import WireError, parse_public_line
from portkey.keys.textio import read_text_normalized
from portkey.models import KnownHostsMatch, Suggestion, SuggestionScan

log = logging.getLogger("portkey.keys")

MAX_KNOWN_HOSTS_BYTES = 8 * 1024 * 1024


class FileKnownHosts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._stamp: tuple[int, int, int] | None = None
        self._entries: list[Entry] = []

    def lookup(self, host: str, port: int) -> KnownHostsMatch:
        name = lookup_name(host, port)
        trusted: list[str] = []
        revoked: list[str] = []
        cas: list[str] = []
        for entry in self.entries():
            if not entry_matches(entry, name):
                continue
            bucket = {"revoked": revoked, "cert-authority": cas}.get(entry.marker or "", trusted)
            if entry.key not in bucket:
                bucket.append(entry.key)
        return KnownHostsMatch(
            trusted=tuple(k for k in trusted if k not in revoked),
            revoked=tuple(revoked),
            cert_authorities=tuple(cas),
        )

    def is_trusted(self, host: str, port: int, public_key: str) -> bool:
        """True when ``public_key`` (``"<type> <base64> [comment]"``) is a plain, non-revoked
        known_hosts key for host:port, hashed entries included."""
        key = _normalize_key(public_key)
        return key is not None and key in self.lookup(host, port).trusted

    def is_revoked(self, host: str, port: int, public_key: str) -> bool:
        key = _normalize_key(public_key)
        return key is not None and key in self.lookup(host, port).revoked

    def suggestions(self) -> SuggestionScan:
        return build_suggestions(self.entries())

    def entries(self) -> list[Entry]:
        """Parsed entries, re-read when the file's mtime, size or inode changes."""
        with self._lock:
            try:
                st = os.stat(self.path)
            except OSError:
                self._stamp, self._entries = None, []
                return []
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
            if stamp != self._stamp:
                self._entries = self._read()
                self._stamp = stamp
            return list(self._entries)

    def _read(self) -> list[Entry]:
        try:
            text = read_text_normalized(self.path, MAX_KNOWN_HOSTS_BYTES)
        except (OSError, ValueError) as exc:
            log.warning("known_hosts not readable (%s)", type(exc).__name__)
            return []
        return parse_known_hosts(text)


def _normalize_key(public_key: str) -> str | None:
    try:
        pub, _ = parse_public_line(public_key)
    except WireError:
        return None
    return pub.openssh


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def build_suggestions(entries: list[Entry]) -> SuggestionScan:
    """Merge plain entries into suggestions: names on the same line, or with the same key at
    the same port, become one suggestion."""
    hashed = sum(1 for e in entries if e.marker is None and e.hashed)
    parent: dict[tuple[str, int], tuple[str, int]] = {}
    order: dict[tuple[str, int], int] = {}
    types: dict[tuple[str, int], set[str]] = {}
    by_key: dict[tuple[bytes, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: tuple[str, int], b: tuple[str, int]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            first, second = sorted((ra, rb), key=order.__getitem__)
            parent[second] = first

    for entry in entries:
        if entry.marker is not None or entry.hashed:
            continue
        nodes = [n for n in (split_plain(p) for p in entry.patterns) if n is not None]
        for node in nodes:
            if node not in parent:
                parent[node] = node
                order[node] = len(order)
            types.setdefault(node, set()).add(entry.key_type)
        for node in nodes:
            # Names on one line at the same port are one host; so are names sharing a key.
            union(next(n for n in nodes if n[1] == node[1]), node)
            union(by_key.setdefault((entry.blob, node[1]), node), node)

    groups: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for node in sorted(parent, key=order.__getitem__):
        groups.setdefault(find(node), []).append(node)

    suggestions: list[Suggestion] = []
    for nodes in groups.values():
        names = [n for n, _ in nodes]
        host = next((n for n in names if not _is_ip(n)), names[0])
        key_types: set[str] = set().union(*(types[n] for n in nodes))
        suggestions.append(
            Suggestion(
                host=host,
                port=nodes[0][1],
                aliases=tuple(n for n in names if n != host),
                key_types=tuple(sorted(key_types)),
            )
        )
    suggestions.sort(key=lambda s: (s.host, s.port))
    return SuggestionScan(suggestions=tuple(suggestions), hashed_count=hashed)
