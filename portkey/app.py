"""App factory (package 0), with a middleware section owned by package 1.

``create_app(config)`` builds the services, registers error handlers, every router and
WebSocket route, ``GET /api/health``, a JSON 404 for unknown ``/api`` paths, and the static
frontend. Run with ``python -m portkey`` (uvicorn ``--factory``).

Only package 1 edits this file, and only between the MIDDLEWARE markers. Everything else is
frozen: a new router or dependency is a contract change, reported to the package-0 owner.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from portkey import __version__
from portkey.api import (
    routes_auth,
    routes_connections,
    routes_diag,
    routes_groups,
    routes_hosts,
    routes_keys,
    routes_sessions,
    routes_settings,
    ws_install,
    ws_terminal,
)
from portkey.api.schemas import HealthResponse
from portkey.config import Config, load_config
from portkey.errors import ErrorCode, PortKeyError
from portkey.services import Services, build_services

log = logging.getLogger("portkey")

ROUTERS = (
    routes_auth.router,
    routes_connections.router,
    routes_groups.router,
    routes_keys.router,
    routes_hosts.router,
    routes_sessions.router,
    routes_settings.router,
    routes_diag.router,
    ws_terminal.router,
    ws_install.router,
)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def portkey_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PortKeyError)
    return JSONResponse({"error": exc.to_body()}, status_code=exc.status)


async def not_implemented_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(501, ErrorCode.NOT_IMPLEMENTED, "This part of Port-Key is not built yet.")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
    return error_response(500, ErrorCode.INTERNAL, "Something went wrong inside Port-Key.")


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    services = build_services(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await services.startup()
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(
        title="Port-Key",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.config = config
    app.state.services = services

    # ===== MIDDLEWARE (package 1 owns this section) ==============================================
    # Host/Origin guard (outermost), security headers, log redaction, and the 422 handler that
    # strips request input. Middleware added last runs first, so add HostGuardMiddleware last.
    from fastapi.exceptions import RequestValidationError

    from portkey.security.headers import SecurityHeadersMiddleware
    from portkey.security.hostguard import HostGuardMiddleware
    from portkey.security.redact import install_log_redaction, validation_exception_handler

    install_log_redaction()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(HostGuardMiddleware, config=config)
    # ===== END MIDDLEWARE =========================================================================

    app.add_exception_handler(PortKeyError, portkey_error_handler)
    app.add_exception_handler(NotImplementedError, not_implemented_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    for router in ROUTERS:
        app.include_router(router)

    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=config.version)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(path: str) -> JSONResponse:
        return error_response(404, ErrorCode.NOT_FOUND, "Not found.")

    app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="static")
    return app


def services_of(app: FastAPI) -> Services:
    """Typed access to ``app.state.services`` (tests, WebSocket handlers)."""
    services: Services = app.state.services
    return services
