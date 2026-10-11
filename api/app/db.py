"""Pool asyncpg y migraciones SQL simples."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_LOCK = 7066001


async def create_pool() -> asyncpg.Pool:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL no configurada")
    last: Exception | None = None
    for _ in range(30):  # la BD puede tardar unos segundos en aceptar conexiones
        try:
            return await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        except (OSError, asyncpg.PostgresError) as exc:
            last = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"No se pudo conectar a PostgreSQL: {type(last).__name__}")


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute("INSERT INTO schema_migrations(version) VALUES ($1)", path.stem)
                applied.append(path.stem)
                log.info("migración aplicada: %s", path.stem)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK)
    return applied
