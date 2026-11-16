"""Auth endpoints: /api/auth/*."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, SecretStr

from portkey.api.schemas.common import Request, Response

NewPassword = Annotated[SecretStr, Field(min_length=8, max_length=1024)]
AnyPassword = Annotated[SecretStr, Field(min_length=1, max_length=1024)]


class AuthStatus(Response):
    setup: bool  # true once an app password exists; false -> show "Set a password"
    authenticated: bool  # true when the request carried a valid bearer token


class SetupRequest(Request):
    password: NewPassword


class LoginRequest(Request):
    password: AnyPassword


class PasswordChangeRequest(Request):
    old: AnyPassword
    new: NewPassword


class TokenResponse(Response):
    token: str
    expires_at: datetime


class HealthResponse(Response):
    status: str = "ok"
    version: str
