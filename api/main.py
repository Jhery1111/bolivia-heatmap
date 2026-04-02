"""
main.py
-------
FastAPI application entry point for the SIGMAC API — DNTT Bolivia.

Startup sequence
----------------
1. structlog is configured for JSON output.
2. The asyncpg connection pool is created.
3. The Redis PubSub listener task is launched.
4. All routers (auth, tiles, heatmap, incidents, websocket) are mounted.
5. Prometheus metrics instrumentation is activated.

Shutdown sequence
-----------------
1. The Redis PubSub listener task is cancelled.
2. The asyncpg pool is closed gracefully.
"""

from __future__ import annotations

import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import router as auth_router
from .config import settings
from .database import close_pool, create_pool
from .routers.heatmap import router as heatmap_router
from .routers.incidents import router as incidents_router
from .routers.tiles import router as tiles_router
from .websocket_handler import router as ws_router
from .websocket_handler import start_redis_listener, stop_redis_listener

# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.LOG_LEVEL.upper(), 20)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of shared resources."""
    logger.info("SIGMAC API starting up", version=settings.APP_VERSION)
    await create_pool()
    await start_redis_listener()
    logger.info("SIGMAC API ready")

    yield  # Application is running

    logger.info("SIGMAC API shutting down")
    await stop_redis_listener()
    await close_pool()
    logger.info("SIGMAC API shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=(
        "API del Sistema Integrado de Gestión y Monitoreo de Accidentes y Conflictos "
        "(SIGMAC) — Dirección Nacional de Tránsito y Transporte, Bolivia.\n\n"
        "Proporciona Vector Tiles MVT, mapas de calor KDE, detección de anomalías "
        "y streaming en tiempo real de incidentes vía WebSocket."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Structured logging for every HTTP request/response."""
    start = time.perf_counter()

    # Bind per-request context visible in all log calls within the same thread.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        method=request.method,
        path=request.url.path,
        client=request.client.host if request.client else "unknown",
    )

    response = await call_next(request)

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "request",
        status_code=response.status_code,
        duration_ms=elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _error_body(status_code: int, detail: Any, path: str) -> dict:
    return {
        "error": {
            "status_code": status_code,
            "detail": detail,
            "path": path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    logger.warning(
        "HTTP exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.status_code, exc.detail, request.url.path),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    logger.warning(
        "Validation error",
        errors=errors,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            errors,
            request.url.path,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    logger.error(
        "Unhandled exception",
        exc_type=type(exc).__name__,
        error=str(exc),
        path=request.url.path,
        traceback=tb,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error",
            request.url.path,
        ),
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(tiles_router)
app.include_router(heatmap_router)
app.include_router(incidents_router)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    response_description="Service liveness status.",
)
async def health_check() -> dict:
    """Return service status, uptime timestamp, and API version."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.APP_VERSION,
        "service": settings.APP_TITLE,
    }


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
    inprogress_labels=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
