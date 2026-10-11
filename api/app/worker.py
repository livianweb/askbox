"""Worker arq: procesa jobs batch."""
from __future__ import annotations

import logging
import os

import redis.asyncio as aioredis
from arq.connections import RedisSettings

from app.batch_jobs import process_job
from app.db import create_pool

log = logging.getLogger(__name__)


async def process_batch_job(ctx, job_id: str) -> None:
    pool = ctx["pool"]
    redis = ctx["redis"]
    log.info("batch job %s iniciado", job_id)
    await process_job(pool, redis, job_id)
    log.info("batch job %s terminado", job_id)


async def startup(ctx) -> None:
    ctx["pool"] = await create_pool()
    ctx["redis"] = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


async def shutdown(ctx) -> None:
    await ctx["redis"].aclose()
    await ctx["pool"].close()


class WorkerSettings:
    functions = [process_batch_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
