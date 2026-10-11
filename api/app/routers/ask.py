from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import ratelimit
from app.config import settings
from app.deps import current_user, err
from app.llm import router as llm_router
from app.llm.errors import LLMError
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["ask"])


class AskIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=200000)
    system: str | None = Field(default=None, max_length=50000)
    project_id: str | None = None
    tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")
    max_output_tokens: int = Field(default=512, ge=1)


async def _log_request(pool, **f) -> str:
    return str(await pool.fetchval(
        "INSERT INTO requests(user_id, project_id, tier_requested, tier_used, status, provider, model_used, key_id, "
        "key_source, prompt_chars, prompt_hash, response_chars, latency_ms, cache_hit, attempts, error_code, error_message) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::uuid,$9,$10,$11,$12,$13,$14,$15,$16,$17) RETURNING id",
        f["user_id"], f.get("project_id"), f["tier_requested"], f.get("tier_used"), f["status"], f.get("provider"),
        f.get("model_used"), f.get("key_id"), f.get("key_source"), f["prompt_chars"], f["prompt_hash"],
        f.get("response_chars"), f.get("latency_ms"), f.get("cache_hit", False), f.get("attempts", 0),
        f.get("error_code"), (f.get("error_message") or None) and f["error_message"][:500],
    ))


@router.post("/ask")
async def ask(body: AskIn, request: Request, user: dict = Depends(current_user)):
    pool, redis = request.app.state.pool, request.app.state.redis
    await ratelimit.check(redis, f"ask:{user['id']}", settings.ask_rate_per_min)

    prompt = body.prompt.strip()
    if not prompt:
        raise err(422, "empty_prompt", "El prompt no puede estar vacío.")
    if len(prompt) > settings.max_prompt_chars:
        raise err(413, "prompt_too_large", f"Prompt demasiado largo (máx {settings.max_prompt_chars} caracteres).")
    if body.system and len(body.system) > settings.max_system_chars:
        raise err(413, "system_too_large", f"System demasiado largo (máx {settings.max_system_chars} caracteres).")
    if body.max_output_tokens > settings.max_output_tokens_cap:
        raise err(422, "max_tokens_too_large", f"max_output_tokens máx {settings.max_output_tokens_cap}.")

    if body.project_id:
        project = await get_project(pool, user["id"], body.project_id)
    else:
        project = await pool.fetchrow("SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at LIMIT 1", user["id"])
        if not project:
            raise err(400, "no_project", "Crea un proyecto primero (POST /v1/projects).")

    messages = []
    if body.system and body.system.strip():
        messages.append({"role": "system", "content": body.system.strip()})
    messages.append({"role": "user", "content": prompt})

    base = dict(
        user_id=user["id"], project_id=project["id"], tier_requested=body.tier,
        prompt_chars=len(prompt), prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
    )
    started = time.monotonic()
    try:
        res = await llm_router.ask(pool, redis, project=dict(project), messages=messages,
                                   tier=body.tier, max_tokens=body.max_output_tokens)
    except llm_router.AskFailure as exc:
        rid = await _log_request(pool, **base, status="error", tier_used=exc.tier_used, attempts=exc.attempts,
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 error_code=exc.code, error_message=str(exc))
        raise err(502, exc.code, f"{exc} (request_id={rid})") from None
    except LLMError as exc:
        rid = await _log_request(pool, **base, status="error", latency_ms=int((time.monotonic() - started) * 1000),
                                 error_code="llm_error", error_message=str(exc))
        raise err(502, "llm_error", f"{exc} (request_id={rid})") from None

    latency = int((time.monotonic() - started) * 1000)
    rid = await _log_request(
        pool, **base, status="ok", tier_used=res["tier_used"], provider=res["provider"], model_used=res["model_used"],
        key_id=res["key_id"], key_source=res["key_source"], response_chars=len(res["text"]), latency_ms=latency,
        cache_hit=res["cache_hit"], attempts=res["attempts"],
    )
    return {
        "request_id": rid, "text": res["text"], "provider": res["provider"], "model_used": res["model_used"],
        "tier_used": res["tier_used"], "cache_hit": res["cache_hit"], "latency_ms": latency,
    }


@router.get("/requests")
async def list_requests(request: Request, limit: int = 20, user: dict = Depends(current_user)):
    limit = max(1, min(limit, 100))
    rows = await request.app.state.pool.fetch(
        "SELECT id, project_id, tier_requested, tier_used, status, provider, model_used, key_source, prompt_chars, "
        "response_chars, latency_ms, cache_hit, attempts, error_code, error_message, created_at "
        "FROM requests WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2", user["id"], limit,
    )
    return [{**{k: (str(v) if k in {"id", "project_id"} and v else v) for k, v in dict(r).items() if k != "created_at"},
             "created_at": r["created_at"].isoformat()} for r in rows]
