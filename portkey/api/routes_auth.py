"""Auth routes (package 1): /api/auth/*. Contract: docs/api.md "Auth".

status, setup and login need no token; logout and password do.

Passwords and the setup code arrive as ``SecretStr`` and are unwrapped only at the call into
``AppAuth``; they are never logged, returned or put in an error message. Status says whether
setup needs the code (``setup_code``), never what it is.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from portkey.api.deps import ServicesDep, TokenDep, client_address
from portkey.api.schemas import (
    AuthStatus,
    LoginRequest,
    PasswordChangeRequest,
    SetupRequest,
    TokenResponse,
)
from portkey.errors import Unauthorized
from portkey.models import IssuedToken

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_out(issued: IssuedToken) -> TokenResponse:
    return TokenResponse(token=issued.token, expires_at=issued.expires_at)


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, services: ServicesDep) -> AuthStatus:
    authorization = request.headers.get("authorization")
    authenticated = False
    if authorization:
        try:
            services.auth.authenticate(authorization)
            authenticated = True
        except Unauthorized:
            authenticated = False
    state = services.auth.setup_code_state()
    return AuthStatus(setup=state is None, authenticated=authenticated, setup_code=state)


@router.post("/setup", response_model=TokenResponse)
def setup(body: SetupRequest, request: Request, services: ServicesDep) -> TokenResponse:
    issued = services.auth.setup(
        body.password.get_secret_value(), body.code.get_secret_value(), client_address(request)
    )
    return _token_out(issued)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, services: ServicesDep) -> TokenResponse:
    issued = services.auth.login(body.password.get_secret_value(), client_address(request))
    return _token_out(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: TokenDep, services: ServicesDep) -> None:
    services.tokens.revoke(token)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChangeRequest, token: TokenDep, services: ServicesDep) -> None:
    services.auth.change_password(token, body.old.get_secret_value(), body.new.get_secret_value())
