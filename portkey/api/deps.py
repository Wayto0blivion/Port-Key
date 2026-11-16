"""FastAPI dependencies shared by every router (package 0, frozen).

- ``ServicesDep``: the app's ``Services``.
- ``require_auth`` / ``TokenDep``: validates ``Authorization: Bearer <token>`` through
  ``AppAuth.authenticate`` (package 1) and returns the token. Protected routers declare
  ``dependencies=[Depends(require_auth)]``; routes that need the token itself take ``TokenDep``.
  Tests that exercise a route before package 1 lands override ``require_auth`` (see the
  ``authed_client`` fixture in tests/conftest.py).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import HTTPConnection

from portkey.services import Services


def get_services(conn: HTTPConnection) -> Services:
    return cast(Services, conn.app.state.services)


ServicesDep = Annotated[Services, Depends(get_services)]


def require_auth(conn: HTTPConnection, services: ServicesDep) -> str:
    return services.auth.authenticate(conn.headers.get("authorization"))


TokenDep = Annotated[str, Depends(require_auth)]


def client_address(conn: HTTPConnection) -> str:
    """Peer address used for login backoff."""
    return conn.client.host if conn.client else "unknown"
