from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import batch_jobs, ratelimit
from app.config import settings
from app.deps import AskAuth, ask_auth, err
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["batch"])


class BatchItemIn(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=200000)
    system: str | None = Field(default=None, max_length=50000)


class BatchIn(BaseModel):
    project_id: str | None = None
    tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")
    max_output_tokens: int = Field(default=512, ge=1)
    items: list[BatchItemIn] = Field(..., min_length=1)


@router.post("/batch")
async def create_batch(body: BatchIn, request: Request, auth: AskAuth = Depends(ask_auth)):
    pool, redis = request.app.state.pool, request.app.state.redis
    max_items = settings.batch_max_items

    if auth.mode == "access_token":
        raise err(403, "forbidden", "El batch requiere sesión de usuario (no token de proyecto).")
    if len(body.items) > max_items:
        raise err(422, "batch_too_large", f"Máximo {max_items} prompts por batch.")

    if body.project_id:
        project = await get_project(pool, auth.user_id, body.project_id)
    else:
        project = await pool.fetchrow(
            "SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at LIMIT 1",
            auth.user_id,
        )
        if not project:
            raise err(400, "no_project", "Crea un proyecto primero.")

    await ratelimit.check(redis, f"batch:{auth.user_id}", settings.ask_rate_per_min)

    if body.max_output_tokens > settings.max_output_tokens_cap:
        raise err(422, "max_tokens_too_large", f"max_output_tokens máx {settings.max_output_tokens_cap}.")

    for item in body.items:
        if len(item.prompt) > settings.max_prompt_chars:
            raise err(413, "prompt_too_large", f"Prompt demasiado largo (máx {settings.max_prompt_chars} caracteres).")

    job_id = await batch_jobs.create_job(
        pool,
        user_id=auth.user_id,
        project_id=project["id"],
        tier=body.tier,
        max_output_tokens=body.max_output_tokens,
        items=[{"prompt": i.prompt.strip(), "system": (i.system or "").strip() or None} for i in body.items],
    )
    arq = request.app.state.arq
    await arq.enqueue_job("process_batch_job", job_id)
    return {"job_id": job_id, "status": "pending", "total": len(body.items)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, auth: AskAuth = Depends(ask_auth)):
    if auth.mode == "access_token":
        raise err(403, "forbidden", "Consulta de jobs solo con sesión de usuario.")
    pool = request.app.state.pool
    job = await pool.fetchrow(
        "SELECT id, project_id, status, tier, max_output_tokens, total_items, completed_items, failed_items, "
        "error_message, created_at, started_at, finished_at FROM batch_jobs WHERE id = $1 AND user_id = $2",
        job_id,
        auth.user_id,
    )
    if not job:
        raise err(404, "job_not_found", "Job no encontrado.")
    items = await pool.fetch(
        "SELECT idx, status, result_text, request_id, error_code, error_message, latency_ms, finished_at "
        "FROM batch_job_items WHERE job_id = $1 ORDER BY idx",
        job_id,
    )
    return {
        "job_id": str(job["id"]),
        "project_id": str(job["project_id"]),
        "status": job["status"],
        "tier": job["tier"],
        "max_output_tokens": job["max_output_tokens"],
        "total": job["total_items"],
        "completed": job["completed_items"],
        "failed": job["failed_items"],
        "error_message": job["error_message"],
        "created_at": job["created_at"].isoformat() if job["created_at"] else None,
        "started_at": job["started_at"].isoformat() if job["started_at"] else None,
        "finished_at": job["finished_at"].isoformat() if job["finished_at"] else None,
        "items": [
            {
                "idx": r["idx"],
                "status": r["status"],
                "text": r["result_text"],
                "request_id": str(r["request_id"]) if r["request_id"] else None,
                "error_code": r["error_code"],
                "error_message": r["error_message"],
                "latency_ms": r["latency_ms"],
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            }
            for r in items
        ],
    }
