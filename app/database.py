"""Small async PostgreSQL store for the latest API data snapshots."""

import json
import logging
from typing import Any

import asyncpg

from .config import settings

logger = logging.getLogger("ltmap.database")
_pool: asyncpg.Pool | None = None


def _database_configured() -> bool:
    return bool(settings.DATABASE_URL) or all(
        (settings.DATABASE_HOST, settings.DATABASE_NAME, settings.DATABASE_USER, settings.DATABASE_PASSWORD)
    )


async def connect() -> None:
    """Open the PostgreSQL pool and create the snapshot table."""
    global _pool

    if not _database_configured():
        if settings.DATABASE_REQUIRED:
            raise RuntimeError("DATABASE_URL or complete DATABASE_* settings are required")
        logger.info("PostgreSQL is not configured; persistent snapshots are disabled")
        return

    pool = None
    try:
        connection_options = (
            {"dsn": settings.DATABASE_URL}
            if settings.DATABASE_URL
            else {
                "host": settings.DATABASE_HOST,
                "port": settings.DATABASE_PORT,
                "database": settings.DATABASE_NAME,
                "user": settings.DATABASE_USER,
                "password": settings.DATABASE_PASSWORD,
            }
        )
        pool = await asyncpg.create_pool(
            **connection_options,
            min_size=1,
            max_size=settings.DATABASE_POOL_SIZE,
            command_timeout=settings.DATABASE_COMMAND_TIMEOUT,
        )
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        _pool = pool
        logger.info("PostgreSQL snapshot store connected")
    except Exception:
        _pool = None
        if pool is not None:
            await pool.close()
        if settings.DATABASE_REQUIRED:
            raise
        logger.exception("PostgreSQL unavailable; continuing without persistent snapshots")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def save_snapshot(key: str, payload: Any) -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            """
            INSERT INTO api_snapshots (snapshot_key, payload, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (snapshot_key) DO UPDATE
            SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            key,
            json.dumps(payload, separators=(",", ":")),
        )
    except Exception:
        logger.exception("Could not persist %s snapshot", key)


async def load_snapshot(key: str) -> Any | None:
    if _pool is None:
        return None
    try:
        payload = await _pool.fetchval(
            "SELECT payload FROM api_snapshots WHERE snapshot_key = $1", key
        )
        if payload is None:
            return None
        return json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        logger.exception("Could not load %s snapshot", key)
        return None


async def status() -> str:
    if _pool is None:
        return "not_configured" if not _database_configured() else "disconnected"
    try:
        await _pool.fetchval("SELECT 1")
        return "connected"
    except Exception:
        return "disconnected"
