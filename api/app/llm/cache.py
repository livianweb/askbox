"""Caché Redis de respuestas. Fail-open: si Redis falla, se ignora."""
from __future__ import annotations

import hashlib
import json
import logging

log = logging.getLogger(__name__)


def cache_key(
    project_id: str,
    prompt_rendered: str,
    tier: str,
    max_tokens: int,
    json_schema: str = "",
    attachment_digest: str = "",
) -> str:
    raw = "\x1f".join([project_id, prompt_rendered, tier, str(max_tokens), json_schema, attachment_digest])
    return "askbox:cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def get(redis, key: str) -> dict | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cache get falló: %s", type(exc).__name__)
        return None


async def put(redis, key: str, value: dict, ttl: int) -> None:
    if redis is None or ttl <= 0:
        return
    try:
        await redis.set(key, json.dumps(value), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache put falló: %s", type(exc).__name__)
