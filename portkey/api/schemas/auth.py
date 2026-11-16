"""Auth endpoints: /api/auth/*."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr

from portkey.api.schemas.common import Request, Response

NewPassword = Annotated[SecretStr, Field(min_length=8, max_length=1024)]
AnyPassword = Annotated[SecretStr, Field(min_length=1, max_length=1024)]
# The one-time code from the startup log, as typed: any case, separators optional.
SetupCode = Annotated[SecretStr, Field(min_length=1, max_length=64)]


class AuthStatus(Response):
    setup: bool  # true once an app password exists; false -> show "Set a password"
    authenticated: bool  # true when the request carried a valid bearer token
    # Never the code itself. null once a password exists; "required": setup needs the code
    # from the startup log; "restart": no code is held until Port-Key restarts.
    setup_code: Literal["required", "restart"] | None = None


class SetupRequest(Request):
    password: NewPassword
    code: SetupCode


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
