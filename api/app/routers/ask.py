from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app import ask_exec, ratelimit
from app.llm import attachments
from app.config import settings
from app.deps import AskAuth, ask_auth, current_user, err
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["ask"])


class AskAttachmentIn(BaseModel):
    mime: str = Field(..., max_length=128)
    data_base64: str = Field(..., max_length=30_000_000)
    filename: str | None = Field(default=None, max_length=255)


class AskIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=200000)
    prompt_template: str | None = Field(default=None, max_length=80)
    variables: dict[str, str] | None = None
    system: str | None = Field(default=None, max_length=50000)
    project_id: str | None = None
    tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")
    max_output_tokens: int = Field(default=512, ge=1)
    json_schema: dict | None = Field(default=None, description="Si se envía, tier=auto lo considera y valida la respuesta JSON.")
    attachments: list[AskAttachmentIn] | None = Field(
        default=None,
        description="Adjuntos inline (imagen o PDF); no se almacenan en servidor.",
    )
    task: str | None = Field(
        default=None,
        pattern="^(transcribe|extract|interpret)$",
        description="Tarea con adjuntos: transcribe | extract | interpret.",
    )

    @model_validator(mode="after")
    def _prompt_or_template(self):
        has_raw = self.prompt is not None and self.prompt.strip()
        has_tpl = self.prompt_template is not None and self.prompt_template.strip()
        if has_raw and has_tpl:
            raise ValueError("Usa prompt o prompt_template, no ambos.")
        if not has_raw and not has_tpl:
            raise ValueError("Indica prompt o prompt_template.")
        return self


async def _prepare_ask(pool, redis, auth: AskAuth, body: AskIn) -> tuple:
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

    messages, prompt, _effective_tier = await ask_exec.resolve_messages(
        pool,
        project,
        prompt=body.prompt,
        prompt_template=body.prompt_template,
        variables=body.variables,
        system=body.system,
        tier=body.tier,
    )

    if len(prompt) > settings.max_prompt_chars:
        raise err(413, "prompt_too_large", f"Prompt demasiado largo (máx {settings.max_prompt_chars} caracteres).")
    if body.max_output_tokens > settings.max_output_tokens_cap:
        raise err(422, "max_tokens_too_large", f"max_output_tokens máx {settings.max_output_tokens_cap}.")

    raw_atts = [a.model_dump() for a in (body.attachments or [])]
    normalized = attachments.normalize_attachments(raw_atts if raw_atts else None)
    task_used = None
    if normalized:
        task_used = attachments.infer_task(
            prompt,
            json_schema=body.json_schema,
            task_requested=body.task,
        )

    return project, messages, prompt, normalized, task_used


@router.post("/ask")
async def ask(body: AskIn, request: Request, auth: AskAuth = Depends(ask_auth)):
    pool, redis = request.app.state.pool, request.app.state.redis
    project, messages, prompt, normalized, task_used = await _prepare_ask(pool, redis, auth, body)
    return await ask_exec.run_ask(
        pool,
        redis,
        project=project,
        user_id=auth.user_id,
        access_token_id=auth.access_token_id,
        tier_requested=body.tier,
        max_output_tokens=body.max_output_tokens,
        messages=messages,
        prompt=prompt,
        json_schema=body.json_schema,
        normalized_attachments=normalized or None,
        task_used=task_used,
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask/stream")
async def ask_stream(body: AskIn, request: Request, auth: AskAuth = Depends(ask_auth)):
    pool, redis = request.app.state.pool, request.app.state.redis
    project, messages, prompt, normalized, task_used = await _prepare_ask(pool, redis, auth, body)

    async def event_stream():
        async for event in ask_exec.iter_stream_events(
            pool,
            redis,
            project=project,
            user_id=auth.user_id,
            access_token_id=auth.access_token_id,
            tier_requested=body.tier,
            max_output_tokens=body.max_output_tokens,
            messages=messages,
            prompt=prompt,
            json_schema=body.json_schema,
            normalized_attachments=normalized or None,
            task_used=task_used,
        ):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
