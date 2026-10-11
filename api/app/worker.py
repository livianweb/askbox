"""Worker arq: procesa jobs batch y cron de auto-update diario."""
from __future__ import annotations

import asyncio
import logging
import os

import redis.asyncio as aioredis
from arq.connections import RedisSettings
from arq.cron import cron

from app.auto_update import cron_hour_utc, cron_minute_utc, run_auto_update
from app.batch_jobs import process_job
from app.config import settings
from app.db import create_pool

log = logging.getLogger(__name__)


async def process_batch_job(ctx, job_id: str) -> None:
    pool = ctx["pool"]
    redis = ctx["redis"]
    log.info("batch job %s iniciado", job_id)
    await process_job(pool, redis, job_id)
    log.info("batch job %s terminado", job_id)


async def cron_auto_update(ctx) -> None:
    log.info("cron auto-update iniciado (repo=%s)", settings.repo_path or "(default)")
    result = await asyncio.to_thread(run_auto_update, settings.repo_path or None, False)
    log.info(
        "cron auto-update terminado ok=%s skipped=%s msg=%s",
        result.get("ok"),
        result.get("skipped"),
        (result.get("message") or "")[:200],
    )


async def startup(ctx) -> None:
    ctx["pool"] = await create_pool()
    ctx["redis"] = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


async def shutdown(ctx) -> None:
    await ctx["redis"].aclose()
    await ctx["pool"].close()


class WorkerSettings:
    functions = [process_batch_job]
    cron_jobs = [
        cron(
            cron_auto_update,
            hour={cron_hour_utc()},
            minute={cron_minute_utc()},
            unique=True,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
