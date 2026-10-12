from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import ask_exec, ratelimit
from app.llm import attachments
from app.config import settings
from app.deps import AskAuth, ask_auth, current_user, err
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["ask"])


class AskAttachmentIn(BaseModel):
    mime: str = Field(
        ...,
        max_length=128,
        description=(
            "Tipo MIME declarado. Solo se aceptan image/png, image/jpeg, image/webp y application/pdf; "
            "el servidor valida el contenido (sniff) y rechaza si no coincide."
        ),
    )
    data_base64: str = Field(
        ...,
        max_length=30_000_000,
        description="Contenido del archivo en base64 (sin prefijo data:). No se persiste en el servidor.",
    )
    filename: str | None = Field(
        default=None,
        max_length=255,
        description="Nombre opcional para trazas y metadatos de respuesta; no afecta al proveedor.",
    )


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
        description=(
            "Adjuntos multimodales inline. Sin este campo el contrato es el de solo texto. "
            "Límites (variables de entorno, defaults entre paréntesis): "
            "ASKBOX_MAX_ATTACHMENTS (8), ASKBOX_MAX_ATTACHMENT_BYTES (5 MiB por archivo), "
            "ASKBOX_MAX_ATTACHMENTS_TOTAL_BYTES (15 MiB en total). "
            "Las imágenes se recomprimen a JPEG (RGB) antes del proveedor: lado mayor ≤ ASKBOX_VISION_MAX_EDGE (1600), "
            "calidad ASKBOX_VISION_JPEG_QUALITY (72); no se agrandan. Los PDF no se recomprimen. "
            "Con adjuntos, el catálogo solo elige modelos con supports_vision (Gemini y OpenRouter según prioridad y claves). "
            "Gemini usa inline_data; OpenRouter, image_url. Los PDF solo los admite Gemini (OpenRouter falla y pasa al siguiente peer)."
        ),
    )
    task: str | None = Field(
        default=None,
        pattern="^(transcribe|extract|interpret)$",
        description=(
            "Solo con adjuntos. transcribe: texto literal; extract: datos estructurados; interpret: descripción o razonamiento. "
            "Si se omite, se infiere del prompt (y de json_schema en extract)."
        ),
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


class AskAttachmentProcessedOut(BaseModel):
    mime: str = Field(description="MIME enviado al proveedor (p. ej. image/jpeg tras preproceso).")
    size_bytes: int = Field(description="Tamaño en bytes tras preproceso (base de caché y digest).")
    filename: str | None = Field(default=None, description="Nombre enviado en la petición, si lo hubo.")


class AskOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str
    text: str
    provider: str
    model_used: str
    tier_used: str
    cache_hit: bool
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    tier_escalated_from: str | None = Field(
        default=None,
        description="Presente si tier=auto escaló una vez por fallo de cascada.",
    )
    models_chain: list[str] | None = Field(
        default=None,
        description="Modelos/proveedores probados en la cascada (etiquetas provider:model).",
    )
    task_used: str | None = Field(
        default=None,
        description="Tarea aplicada a los adjuntos (transcribe, extract o interpret). Solo si hubo adjuntos.",
    )
    attachments_processed: list[AskAttachmentProcessedOut] | None = Field(
        default=None,
        description="Metadatos por adjunto tras validación y preproceso. Solo si hubo adjuntos.",
    )


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


@router.post(
    "/ask",
    response_model=AskOut,
    summary="Completar prompt (texto o multimodal)",
    description=(
        "Auth: cookie de sesión o `Authorization: Bearer ab_live_…` (scope ask:read). "
        "Cuerpo: `prompt` o `prompt_template` + `variables`, más tier, tokens y opcionalmente adjuntos. "
        "Los adjuntos no se guardan; la caché Redis incluye un digest de los bytes enviados al proveedor."
    ),
)
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


@router.post(
    "/ask/stream",
    summary="Completar prompt con SSE",
    description=(
        "Mismo cuerpo y reglas de adjuntos que POST /v1/ask. "
        "Respuesta text/event-stream: eventos `delta` (text) y `done` (metadatos, incl. task_used y attachments_processed si aplica)."
    ),
)
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
