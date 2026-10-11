"""Rate limit fijo por ventana de 60 s en Redis. Fail-open si Redis no responde."""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException

log = logging.getLogger(__name__)


async def check(redis, bucket: str, limit: int, window: int = 60) -> None:
    if redis is None or limit <= 0:
        return
    key = f"askbox:rl:{bucket}:{int(time.time() // window)}"
    try:
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, window + 5)
    except Exception as exc:  # noqa: BLE001
        log.warning("rate limit no disponible: %s", type(exc).__name__)
        return
    if n > limit:
        raise HTTPException(429, {"code": "rate_limited", "message": "Demasiadas peticiones; espera un minuto."},
                            headers={"Retry-After": str(window)})
