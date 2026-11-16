"""The app container's log, as the owner reads it with ``docker compose logs portkey``.

While no app password exists, the app prints a one-time setup code to its log at startup and
nowhere else; first-visit setup needs it. The e2e container has no Docker access (the Docker
socket would hand it the whole host), so ``./mk e2e`` runs ``docker compose logs app`` once the
app is healthy and passes the output in ``$PORTKEY_APP_LOG``. Against a stack you keep up::

    docker compose -f compose.dev.yaml -p <project> --profile e2e up -d --wait app evil-origin
    PORTKEY_APP_LOG="$(docker compose -f compose.dev.yaml -p <project> --profile e2e \\
        logs --no-color app)" docker compose -f compose.dev.yaml -p <project> --profile e2e \\
        run --rm --no-deps e2e pytest -p no:cacheprovider -m e2e tests/e2e/test_00_first_run.py

The code is read the way a person reads it: the newest "Setup code:" line, whatever prefix
``docker compose logs`` puts in front of it. The pattern is written out here rather than taken
from the app, so a change to what the app prints shows up as a failing spec.
"""

from __future__ import annotations

import os
import re

#: "Setup code:   K7QM-3XRP-W9TD": three groups of four from 2-9 and A-Z without I and O.
_SYMBOL = "[2-9A-HJ-NP-Z]"
SETUP_CODE_LINE = re.compile(
    rf"Setup code:\s+({_SYMBOL}{{4}}-{_SYMBOL}{{4}}-{_SYMBOL}{{4}})\s*$", re.M
)


def app_log() -> str:
    """What ``docker compose logs app`` showed once the app was healthy ("" if not passed)."""
    return os.environ.get("PORTKEY_APP_LOG", "")


def setup_code_from_log(log: str | None = None) -> str:
    """The newest setup code in the app's log. Raises ``LookupError`` when there is none."""
    found = SETUP_CODE_LINE.findall(app_log() if log is None else log)
    if not found:
        raise LookupError(
            "No setup code in the app's log ($PORTKEY_APP_LOG). Run ./mk e2e, or pass the output"
            " of `docker compose ... logs --no-color app` as PORTKEY_APP_LOG (see"
            " tests/e2e/harness/app_log.py)."
        )
    code: str = found[-1]
    return code
