"""Clear the app password: ``python -m portkey.reset_password --yes`` inside the container.

For an owner who has forgotten the Port-Key password. On the PC::

    docker compose exec portkey python -m portkey.reset_password --yes

It deletes the stored password hash and every sign-in token in one transaction, so every
browser is signed out and the next visit shows "Set a password for Port-Key". Connections, keys,
trusted host keys and settings are kept. Until a new password is set, the first local process
to reach the port can set it, so set one straight away.

Safe while the app runs: the app reads the password and tokens from the database on each use.
Only someone who can run commands in the container (that is, who controls Docker on the PC) can
do this, and they could read the database anyway.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from portkey.config import load_config
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
    print(
        f"The app password is cleared and {revoked} sign-in(s) ended. Open Port-Key now and "
        "set a new password."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
