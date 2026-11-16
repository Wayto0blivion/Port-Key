"""The one-time setup code that guards first-visit password setup.

While no app password exists, ``POST /api/auth/setup`` is the only door into Port-Key, and it
needs no sign-in. Any local process that reaches the port before the owner does (another
Windows account, a WSL distro, local software) could otherwise set the password and use the
owner's SSH keys. So the app holds a one-time setup code:

- generated with ``secrets`` at startup while no password exists: 12 symbols from a 32-symbol
  alphabet without look-alikes (no 0/O, no 1/I), 60 bits, shown as ``XXXX-XXXX-XXXX``;
- held in memory only (``PasswordAuth``), regenerated on every start until a password is set,
  and dropped once it is;
- printed once, in one framed record on the ``portkey.setup`` logger (``announce``), which
  ``__main__`` sends to the container log. That record is the only place the code appears:
  never an API response, an event record or another log line (the redaction filter scrubs
  it from every other record as a backstop).

``matches`` accepts the code in any case, with or without separators, and compares in
constant time. Wrong codes share the login backoff (``PasswordAuth.setup``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from portkey.security.redact import SETUP_CODE_LOGGER

#: 32 symbols, so each is exactly 5 bits: digits 2-9 and A-Z without I and O.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
GROUPS = 3
GROUP_LEN = 4
LENGTH = GROUPS * GROUP_LEN
#: Bits of entropy: 12 symbols x 5 bits.
BITS = LENGTH * (len(ALPHABET).bit_length() - 1)

#: What may sit between symbols when the owner types or pastes the code: white space, hyphens
#: (ASCII and the Unicode dashes U+2010 to U+2015), underscores and dots.
_SEPARATORS = re.compile("[\\s\\-_.\\u2010-\\u2015]+")

#: The service name in compose.yaml, for "docker compose logs portkey" in messages and the UI.
COMPOSE_SERVICE = "portkey"

LOGGER_NAME = SETUP_CODE_LOGGER  # "portkey.setup", exempt from scrubbing the code
log = logging.getLogger(LOGGER_NAME)
# The announcement must reach the container log whatever the root logger's level is.
log.setLevel(logging.INFO)

#: Matches the code line of the announcement (also after a ``docker compose logs`` prefix).
_GROUP = f"[{ALPHABET}]{{{GROUP_LEN}}}"
LOG_LINE = re.compile(rf"Setup code:\s+({_GROUP}(?:-{_GROUP}){{{GROUPS - 1}}})")

_RULE = "=" * 72
_ANNOUNCEMENT = "\n".join(
    [
        "",
        _RULE,
        "  Setup code:   %s",
        "",
        "  Port-Key has no app password yet. Open %s on this PC and type",
        '  this code in the "Setup code" field, then choose a password.',
        "  The code works once. Until a password is set, Port-Key prints a new",
        "  code each time it starts; only the newest one works.",
        _RULE,
    ]
)


def generate() -> str:
    """A fresh code such as ``K7QM-3XRP-W9TD`` (CSPRNG, 60 bits)."""
    symbols = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return "-".join(symbols[i : i + GROUP_LEN] for i in range(0, LENGTH, GROUP_LEN))


def normalize(text: str) -> str:
    """Upper-case with every separator removed: ``k7qm 3xrp-w9td`` -> ``K7QM3XRPW9TD``."""
    return _SEPARATORS.sub("", text).upper()


def _digest(text: str) -> bytes:
    return hashlib.sha256(normalize(text).encode("utf-8", "surrogatepass")).digest()


def matches(candidate: str, expected: str) -> bool:
    """Constant-time comparison of what the owner typed with the held code. Both sides are
    hashed first, so neither the length nor the content of the code leaks through timing."""
    return hmac.compare_digest(_digest(candidate), _digest(expected))


def announce(code: str, url: str) -> None:
    """Print ``code`` once, framed, on the ``portkey.setup`` logger."""
    log.info(_ANNOUNCEMENT, code, url)


def find_in_log(text: str) -> str | None:
    """The newest setup code in a container log (``docker compose logs portkey``), or None."""
    found = LOG_LINE.findall(text)
    return found[-1] if found else None
