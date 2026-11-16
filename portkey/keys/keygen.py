"""Key generation (package 3).

``asyncssh.generate_private_key`` plus ``export_private_key("openssh", passphrase)``; files go
to /keys-generated with ``O_CREAT | O_EXCL`` to a temporary name and an atomic rename, LF line
endings, never overwriting and never deleting. Default comment: ``<user>@<host> port-key
<date>`` is built by the frontend, not here.
"""

from __future__ import annotations

import re
from pathlib import Path

from portkey.models import KeyGenSpec

KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")


def validate_key_name(name: str) -> bool:
    """True if ``name`` is an allowed file name (no path separators, no ``.pub``/``.ppk``)."""
    return bool(KEY_NAME_RE.match(name)) and not name.endswith((".pub", ".ppk"))


def generate_key_files(spec: KeyGenSpec, dest_dir: Path) -> Path:
    """Write ``<name>`` and ``<name>.pub`` into ``dest_dir`` and return the private key path.
    Raises ``Conflict(key_name_taken)`` if either file exists, ``Invalid(key_name_invalid)``.
    Blocking."""
    raise NotImplementedError("package 3")
