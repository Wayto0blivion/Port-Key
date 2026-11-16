"""Read-only known_hosts parsing (package 3). Implements ``interfaces.KnownHostsReader``.

Reads ``/ssh/known_hosts`` through ``textio.read_text_normalized`` and re-reads it when the
mtime changes. Handles plain and hashed (``|1|``) names, ``[host]:port``, ``@cert-authority`` and
``@revoked`` markers, and wildcards (matched, never suggested). Suggestions merge entries that
share a key; the preferred ``host`` is a name before an IP address; hashed entries are only
counted. The owner's file is never written.
"""

from __future__ import annotations

from pathlib import Path

from portkey.models import KnownHostsMatch, SuggestionScan


class FileKnownHosts:
    def __init__(self, path: Path) -> None:
        self.path = path

    def lookup(self, host: str, port: int) -> KnownHostsMatch:
        raise NotImplementedError("package 3")

    def suggestions(self) -> SuggestionScan:
        raise NotImplementedError("package 3")
