"""
database.py
-----------
Database connection management for SIGMAC API.

* asyncpg pool  — used by all async FastAPI path operations.
* psycopg2 conn — used by HeatmapEngine (synchronous NumPy/SciPy pipeline).

Lifecycle (startup / shutdown) is registered on the FastAPI application
instance in main.py via the `lifespan` context manager.
"""

from __future__ import annotations

import contextlib
from typing import AsyncGenerator, Generator

import asyncpg
import psycopg2
import psycopg2.extras
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level pool holder — populated during app startup.
# ---------------------------------------------------------------------------
_async_pool: asyncpg.Pool | None = None


# ---------------------------------------------------------------------------
# Startup / shutdown helpers (called from main.py lifespan)
# ---------------------------------------------------------------------------

async def create_pool() -> None:
    """Initialise the asyncpg connection pool.  Called once at startup."""
    global _async_pool

    logger.info(
        "Creating asyncpg pool",
        dsn=settings.DATABASE_URL.split("@")[-1],  # hide credentials in log
        min_size=5,
        max_size=20,
    )
    _async_pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=5,
        max_size=20,
        command_timeout=30,
        statement_cache_size=100,
    )
    logger.info("asyncpg pool ready")


async def close_pool() -> None:
    """Gracefully close the asyncpg pool.  Called once at shutdown."""
    global _async_pool
    if _async_pool is not None:
        await _async_pool.close()
        _async_pool = None
        logger.info("asyncpg pool closed")


# ---------------------------------------------------------------------------
# FastAPI async dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI dependency that yields an asyncpg connection from the pool.

    Usage::

        @router.get("/example")
        async def handler(conn: asyncpg.Connection = Depends(get_db)):
            ...
    """
    if _async_pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Ensure create_pool() was called during app startup."
        )
    async with _async_pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Synchronous psycopg2 connection helper (used by HeatmapEngine)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def get_sync_db() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context-manager that opens/closes a psycopg2 connection.

    Intended for the synchronous HeatmapEngine which relies on cursor-based
    iteration.  Each call opens a fresh connection; callers should not hold
    these open across multiple requests — wrap tightly around the engine call.

    Usage::

        with get_sync_db() as conn:
            engine = HeatmapEngine(conn)
            result = engine.compute_heatmap(...)
    """
    conn: psycopg2.extensions.connection | None = None
    try:
        conn = psycopg2.connect(
            settings.DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
            connect_timeout=10,
        )
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    except psycopg2.OperationalError as exc:
        logger.error("psycopg2 connection failed", error=str(exc))
        raise
    finally:
        if conn is not None and not conn.closed:
            conn.close()
