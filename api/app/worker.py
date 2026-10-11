"""Worker arq (stub Fase 0): sin jobs todavía."""
import os

from arq.connections import RedisSettings


async def noop(ctx) -> None:
    return None


class WorkerSettings:
    functions = [noop]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
