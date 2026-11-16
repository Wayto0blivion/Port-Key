"""Clear the app password: ``python -m portkey.reset_password --yes`` inside the container.

For an owner who has forgotten the Port-Key password. On the PC::

    docker compose exec portkey python -m portkey.reset_password --yes

It deletes the stored password hash and every sign-in token in one transaction, so every
browser is signed out and the next visit shows "Set a password for Port-Key". Connections, keys,
trusted host keys and settings are kept.

Setting the new password needs a fresh one-time setup code, so a reset does not reopen
first-visit setup to any local process. The running app reads the password and tokens from the
database on each use, so it sees the reset at once; but it made no setup code at startup (a
password existed then), so it refuses setup with ``setup_needs_restart`` until it restarts. The
next start prints a new code to the container log::

    docker compose restart portkey
    docker compose logs portkey

Only someone who can run commands in the container (that is, who controls Docker on the PC) can
do this, and they could read the database anyway.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from portkey.config import load_config
from portkey.security import setup_code
from portkey.store.db import Database


def reset(db_path: Path) -> int:
    """Delete the password and every token. Returns how many tokens were revoked."""
    db = Database(db_path)
    db.migrate()
    try:
        with db.tx() as c:
            c.execute("DELETE FROM app_auth")
            return int(c.execute("DELETE FROM tokens").rowcount)
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m portkey.reset_password",
        description="Clear the Port-Key app password and sign out every browser.",
    )
    parser.add_argument("--yes", action="store_true", help="really clear it")
    args = parser.parse_args(argv)
    db_path = load_config().db_path
    if not db_path.exists():
        print(f"No Port-Key database at {db_path}. Nothing to reset.", file=sys.stderr)
        return 1
    if not args.yes:
        print(
            "This clears the Port-Key app password and signs out every browser. Connections, "
            "keys and settings are kept.\nRun it again with --yes to go ahead.",
            file=sys.stderr,
        )
        return 2
    revoked = reset(db_path)
    service = setup_code.COMPOSE_SERVICE
    print(
        f"The app password is cleared and {revoked} sign-in(s) ended.\n"
        "To set a new password, Port-Key needs a new setup code. On this PC run\n"
        f"    docker compose restart {service}\n"
        f"    docker compose logs {service}\n"
        "then open Port-Key, type the newest setup code from the log and set a new password."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
