"""Worker arq: procesa jobs batch y cron de descubrimiento de modelos free."""
from __future__ import annotations

import logging
import os

import redis.asyncio as aioredis
from arq.connections import RedisSettings
from arq.cron import cron

from app.batch_jobs import process_job
from app.catalog_sync_check import format_report_text, run_sync_check, save_report
from app.config import settings
from app.db import create_pool, run_migrations

log = logging.getLogger(__name__)


async def process_batch_job(ctx, job_id: str) -> None:
    pool = ctx["pool"]
    redis = ctx["redis"]
    log.info("batch job %s iniciado", job_id)
    await process_job(pool, redis, job_id)
    log.info("batch job %s terminado", job_id)


async def catalog_free_models_check(ctx) -> None:
    """Descubrimiento diario: diff + sondeos; escribe .askbox/catalog-sync-latest.json. Sin --apply."""
    pool = ctx["pool"]
    await run_migrations(pool)
    report = await run_sync_check(
        pool,
        probe=True,
        probe_limit=settings.catalog_sync_probe_limit,
    )
    saved = save_report(report)
    summary = format_report_text(report)
    for line in summary.splitlines()[:20]:
        log.info("catalog-sync | %s", line)
    if report.not_in_catalog:
        log.warning(
            "catalog-sync | %d candidato(s) free fuera de model_catalog (revisión humana; no auto-enable)",
            len(report.not_in_catalog),
        )
    if saved:
        log.info("catalog-sync | informe: %s", saved)
    for err in report.errors:
        log.warning("catalog-sync | %s", err)


def _cron_jobs() -> list:
    if not settings.catalog_sync_cron:
        return []
    return [
        cron(
            catalog_free_models_check,
            name="catalog_free_models_daily",
            hour=6,
            minute=0,
            unique=True,
        ),
    ]


async def startup(ctx) -> None:
    ctx["pool"] = await create_pool()
    ctx["redis"] = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    if settings.catalog_sync_cron:
        log.info("catalog-sync cron: diario 06:00 UTC (ASKBOX_CATALOG_SYNC_CRON=true)")


async def shutdown(ctx) -> None:
    await ctx["redis"].aclose()
    await ctx["pool"].close()


class WorkerSettings:
    functions = [process_batch_job, catalog_free_models_check]
    cron_jobs = _cron_jobs()
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
