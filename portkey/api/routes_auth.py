"""Auth routes (package 1): /api/auth/*. Contract: docs/api.md "Auth".

status, setup and login need no token; logout and password do.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from portkey.api.deps import ServicesDep, TokenDep
from portkey.api.schemas import (
    AuthStatus,
    LoginRequest,
    PasswordChangeRequest,
    SetupRequest,
    TokenResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, services: ServicesDep) -> AuthStatus:
    raise NotImplementedError("package 1")


@router.post("/setup", response_model=TokenResponse)
def setup(body: SetupRequest, services: ServicesDep) -> TokenResponse:
    raise NotImplementedError("package 1")


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, services: ServicesDep) -> TokenResponse:
    raise NotImplementedError("package 1")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: TokenDep, services: ServicesDep) -> None:
    raise NotImplementedError("package 1")


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChangeRequest, token: TokenDep, services: ServicesDep) -> None:
    raise NotImplementedError("package 1")
