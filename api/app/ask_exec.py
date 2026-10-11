"""Ejecución compartida de /v1/ask (síncrona, batch worker y stream)."""
from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import HTTPException

from app import usage as usage_agg
from app.deps import err
from app.llm import router as llm_router
from app.llm.errors import LLMError
from app.prompt_render import PromptRenderError, render_template


async def log_request(pool, **f) -> str:
    return str(
        await pool.fetchval(
            "INSERT INTO requests(user_id, project_id, access_token_id, tier_requested, tier_used, status, provider, model_used, key_id, "
            "key_source, prompt_chars, prompt_hash, response_chars, latency_ms, cache_hit, attempts, "
            "input_tokens, output_tokens, estimated_cost_usd, error_code, error_message) "
            "VALUES ($1,$2,$3::uuid,$4,$5,$6,$7,$8,$9::uuid,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21) RETURNING id",
            f["user_id"],
            f.get("project_id"),
            f.get("access_token_id"),
            f["tier_requested"],
            f.get("tier_used"),
            f["status"],
            f.get("provider"),
            f.get("model_used"),
            f.get("key_id"),
            f.get("key_source"),
            f["prompt_chars"],
            f["prompt_hash"],
            f.get("response_chars"),
            f.get("latency_ms"),
            f.get("cache_hit", False),
            f.get("attempts", 0),
            f.get("input_tokens"),
            f.get("output_tokens"),
            f.get("estimated_cost_usd"),
            f.get("error_code"),
            (f.get("error_message") or None) and f["error_message"][:500],
        )
    )


async def resolve_messages(pool, project, *, prompt: str | None, prompt_template: str | None, variables: dict | None, system: str | None, tier: str):
    tier_hint = tier
    if prompt_template and prompt_template.strip():
        name = prompt_template.strip()
        row = await pool.fetchrow(
            "SELECT system_prompt, user_template, default_tier FROM project_prompts "
            "WHERE project_id = $1 AND name = $2 ORDER BY version DESC LIMIT 1",
            project["id"],
            name,
        )
        if not row:
            raise err(404, "prompt_not_found", f"Plantilla «{name}» no encontrada en el proyecto.")
        try:
            user_text = render_template(row["user_template"], variables).strip()
            system_text = render_template(row["system_prompt"], variables).strip() if row["system_prompt"] else ""
        except PromptRenderError as exc:
            raise err(422, "prompt_variables", str(exc)) from None
        if system and system.strip():
            system_text = system.strip()
        if tier_hint == "auto" and row["default_tier"] != "auto":
            tier_hint = row["default_tier"]
    else:
        user_text = (prompt or "").strip()
        system_text = system.strip() if system and system.strip() else ""

    if not user_text:
        raise err(422, "empty_prompt", "El prompt no puede estar vacío.")

    messages = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    return messages, user_text, tier_hint


def _base_fields(user_id, project, access_token_id, tier_requested, prompt: str) -> dict:
    return dict(
        user_id=user_id,
        project_id=project["id"],
        access_token_id=access_token_id,
        tier_requested=tier_requested,
        prompt_chars=len(prompt),
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
    )


async def _invoke_llm(pool, redis, *, project, messages, tier_requested, max_output_tokens, json_schema, base: dict, started: float):
    try:
        return await llm_router.ask(
            pool,
            redis,
            project=dict(project),
            messages=messages,
            tier=tier_requested,
            max_tokens=max_output_tokens,
            json_schema=json_schema,
        )
    except llm_router.AskFailure as exc:
        latency_err = int((time.monotonic() - started) * 1000)
        rid = await log_request(
            pool,
            **base,
            status="error",
            tier_used=exc.tier_used,
            attempts=exc.attempts,
            latency_ms=latency_err,
            error_code=exc.code,
            error_message=str(exc),
        )
        await usage_agg.record(pool, project_id=project["id"], ok=False, cache_hit=False)
        raise err(502, exc.code, f"{exc} (request_id={rid})") from None
    except LLMError as exc:
        rid = await log_request(
            pool,
            **base,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code="llm_error",
            error_message=str(exc),
        )
        raise err(502, "llm_error", f"{exc} (request_id={rid})") from None


def _response_from_res(res: dict, rid: str, latency: int) -> dict:
    out = {
        "request_id": rid,
        "text": res["text"],
        "provider": res["provider"],
        "model_used": res["model_used"],
        "tier_used": res["tier_used"],
        "cache_hit": res["cache_hit"],
        "latency_ms": latency,
        "input_tokens": res.get("input_tokens") or 0,
        "output_tokens": res.get("output_tokens") or 0,
        "estimated_cost_usd": res.get("estimated_cost_usd") or 0.0,
    }
    if res.get("tier_escalated_from"):
        out["tier_escalated_from"] = res["tier_escalated_from"]
    if res.get("models_chain"):
        out["models_chain"] = res["models_chain"]
    return out


async def _finalize_success(pool, project, access_token_id, base: dict, res: dict, started: float) -> dict:
    latency = int((time.monotonic() - started) * 1000)
    rid = await log_request(
        pool,
        **base,
        status="ok",
        tier_used=res["tier_used"],
        provider=res["provider"],
        model_used=res["model_used"],
        key_id=res["key_id"],
        key_source=res["key_source"],
        response_chars=len(res["text"]),
        latency_ms=latency,
        cache_hit=res["cache_hit"],
        attempts=res["attempts"],
        input_tokens=res.get("input_tokens"),
        output_tokens=res.get("output_tokens"),
        estimated_cost_usd=res.get("estimated_cost_usd"),
    )
    await usage_agg.record(
        pool,
        project_id=project["id"],
        ok=True,
        cache_hit=bool(res["cache_hit"]),
        input_tokens=int(res.get("input_tokens") or 0),
        output_tokens=int(res.get("output_tokens") or 0),
        estimated_cost_usd=float(res.get("estimated_cost_usd") or 0),
    )
    if access_token_id:
        await pool.execute(
            "UPDATE project_access_tokens SET last_used_at = $2 WHERE id = $1",
            access_token_id,
            datetime.now(timezone.utc),
        )
    return _response_from_res(res, rid, latency)


async def run_ask(
    pool,
    redis,
    *,
    project: dict,
    user_id,
    access_token_id=None,
    tier_requested: str,
    max_output_tokens: int,
    messages: list[dict],
    prompt: str,
    json_schema: dict | None = None,
) -> dict:
    base = _base_fields(user_id, project, access_token_id, tier_requested, prompt)
    started = time.monotonic()
    res = await _invoke_llm(
        pool, redis, project=project, messages=messages, tier_requested=tier_requested,
        max_output_tokens=max_output_tokens, json_schema=json_schema, base=base, started=started,
    )
    return await _finalize_success(pool, project, access_token_id, base, res, started)


async def iter_stream_events(
    pool,
    redis,
    *,
    project: dict,
    user_id,
    access_token_id=None,
    tier_requested: str,
    max_output_tokens: int,
    messages: list[dict],
    prompt: str,
    json_schema: dict | None = None,
    chunk_size: int = 64,
) -> AsyncIterator[dict]:
    """Una sola llamada LLM; emite deltas y un evento done con metadatos."""
    base = _base_fields(user_id, project, access_token_id, tier_requested, prompt)
    started = time.monotonic()
    try:
        res = await _invoke_llm(
            pool, redis, project=project, messages=messages, tier_requested=tier_requested,
            max_output_tokens=max_output_tokens, json_schema=json_schema, base=base, started=started,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "stream_error", "message": str(exc.detail)}
        yield {"type": "error", "code": detail.get("code"), "message": detail.get("message")}
        return

    text = res["text"] or ""
    for i in range(0, len(text), chunk_size):
        yield {"type": "delta", "text": text[i : i + chunk_size]}
    out = await _finalize_success(pool, project, access_token_id, base, res, started)
    yield {"type": "done", **{k: v for k, v in out.items() if k != "text"}}
