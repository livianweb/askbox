from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from app import ratelimit
from app.config import settings
from app.deps import AskAuth, ask_auth, current_user, err
from app.llm import router as llm_router
from app.llm.errors import LLMError
from app.prompt_render import PromptRenderError, render_template
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["ask"])


class AskIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=200000)
    prompt_template: str | None = Field(default=None, max_length=80)
    variables: dict[str, str] | None = None
    system: str | None = Field(default=None, max_length=50000)
    project_id: str | None = None
    tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")
    max_output_tokens: int = Field(default=512, ge=1)

    @model_validator(mode="after")
    def _prompt_or_template(self):
        has_raw = self.prompt is not None and self.prompt.strip()
        has_tpl = self.prompt_template is not None and self.prompt_template.strip()
        if has_raw and has_tpl:
            raise ValueError("Usa prompt o prompt_template, no ambos.")
        if not has_raw and not has_tpl:
            raise ValueError("Indica prompt o prompt_template.")
        return self


async def _log_request(pool, **f) -> str:
    return str(await pool.fetchval(
        "INSERT INTO requests(user_id, project_id, access_token_id, tier_requested, tier_used, status, provider, model_used, key_id, "
        "key_source, prompt_chars, prompt_hash, response_chars, latency_ms, cache_hit, attempts, error_code, error_message) "
        "VALUES ($1,$2,$3::uuid,$4,$5,$6,$7,$8,$9::uuid,$10,$11,$12,$13,$14,$15,$16,$17,$18) RETURNING id",
        f["user_id"], f.get("project_id"), f.get("access_token_id"), f["tier_requested"], f.get("tier_used"), f["status"],
        f.get("provider"), f.get("model_used"), f.get("key_id"), f.get("key_source"), f["prompt_chars"], f["prompt_hash"],
        f.get("response_chars"), f.get("latency_ms"), f.get("cache_hit", False), f.get("attempts", 0),
        f.get("error_code"), (f.get("error_message") or None) and f["error_message"][:500],
    ))


async def _resolve_messages(pool, project, body: AskIn) -> tuple[list[dict], str, str]:
    """messages, user prompt text (for logging), effective tier hint from template."""
    tier_hint = body.tier
    if body.prompt_template and body.prompt_template.strip():
        name = body.prompt_template.strip()
        row = await pool.fetchrow(
            "SELECT system_prompt, user_template, default_tier FROM project_prompts "
            "WHERE project_id = $1 AND name = $2 ORDER BY version DESC LIMIT 1",
            project["id"], name,
        )
        if not row:
            raise err(404, "prompt_not_found", f"Plantilla «{name}» no encontrada en el proyecto.")
        try:
            user_text = render_template(row["user_template"], body.variables).strip()
            system_text = render_template(row["system_prompt"], body.variables).strip() if row["system_prompt"] else ""
        except PromptRenderError as exc:
            raise err(422, "prompt_variables", str(exc)) from None
        if body.system and body.system.strip():
            system_text = body.system.strip()
        if tier_hint == "auto" and row["default_tier"] != "auto":
            tier_hint = row["default_tier"]
    else:
        user_text = (body.prompt or "").strip()
        system_text = body.system.strip() if body.system and body.system.strip() else ""

    if not user_text:
        raise err(422, "empty_prompt", "El prompt no puede estar vacío.")

    messages = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    return messages, user_text, tier_hint


@router.post("/ask")
async def ask(body: AskIn, request: Request, auth: AskAuth = Depends(ask_auth)):
    pool, redis = request.app.state.pool, request.app.state.redis

    if auth.mode == "session":
        await ratelimit.check(redis, f"ask:{auth.user_id}", settings.ask_rate_per_min)
    if auth.access_token_id:
        await ratelimit.check(redis, f"ask:token:{auth.access_token_id}", settings.ask_rate_per_min)

    if auth.mode == "access_token":
        if body.project_id:
            raise err(422, "project_id_ignored", "Con token de proyecto no envíes project_id.")
        project = await pool.fetchrow("SELECT * FROM projects WHERE id = $1", auth.project_id)
    elif body.project_id:
        project = await get_project(pool, auth.user_id, body.project_id)
    else:
        project = await pool.fetchrow(
            "SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at LIMIT 1", auth.user_id,
        )
        if not project:
            raise err(400, "no_project", "Crea un proyecto primero (POST /v1/projects).")

    await ratelimit.check(redis, f"ask:project:{project['id']}", settings.ask_rate_per_min)

    messages, prompt, effective_tier = await _resolve_messages(pool, project, body)

    if len(prompt) > settings.max_prompt_chars:
        raise err(413, "prompt_too_large", f"Prompt demasiado largo (máx {settings.max_prompt_chars} caracteres).")
    if body.max_output_tokens > settings.max_output_tokens_cap:
        raise err(422, "max_tokens_too_large", f"max_output_tokens máx {settings.max_output_tokens_cap}.")

    base = dict(
        user_id=auth.user_id,
        project_id=project["id"],
        access_token_id=auth.access_token_id,
        tier_requested=body.tier,
        prompt_chars=len(prompt),
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
    )
    started = time.monotonic()
    tier_for_llm = effective_tier if body.tier == "auto" else body.tier
    try:
        res = await llm_router.ask(
            pool, redis, project=dict(project), messages=messages,
            tier=tier_for_llm, max_tokens=body.max_output_tokens,
        )
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
    if auth.access_token_id:
        await pool.execute(
            "UPDATE project_access_tokens SET last_used_at = $2 WHERE id = $1",
            auth.access_token_id, datetime.now(timezone.utc),
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
