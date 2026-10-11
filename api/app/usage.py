"""Agregación diaria de uso por proyecto."""
from __future__ import annotations

from datetime import date


async def record(
    pool,
    *,
    project_id,
    ok: bool,
    cache_hit: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    day: date | None = None,
) -> None:
    d = day or date.today()
    await pool.execute(
        "INSERT INTO usage_daily(project_id, day, requests_ok, requests_error, cache_hits, "
        "input_tokens, output_tokens, estimated_cost_usd) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (project_id, day) DO UPDATE SET "
        "requests_ok = usage_daily.requests_ok + EXCLUDED.requests_ok, "
        "requests_error = usage_daily.requests_error + EXCLUDED.requests_error, "
        "cache_hits = usage_daily.cache_hits + EXCLUDED.cache_hits, "
        "input_tokens = usage_daily.input_tokens + EXCLUDED.input_tokens, "
        "output_tokens = usage_daily.output_tokens + EXCLUDED.output_tokens, "
        "estimated_cost_usd = usage_daily.estimated_cost_usd + EXCLUDED.estimated_cost_usd",
        project_id,
        d,
        1 if ok else 0,
        0 if ok else 1,
        1 if cache_hit and ok else 0,
        max(0, input_tokens),
        max(0, output_tokens),
        max(0.0, estimated_cost_usd),
    )
