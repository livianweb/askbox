"""Creación y procesamiento de jobs batch (worker arq)."""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app import ask_exec

log = logging.getLogger(__name__)


async def create_job(
    pool,
    *,
    user_id,
    project_id,
    tier: str,
    max_output_tokens: int,
    items: list[dict],
) -> str:
    job_id = await pool.fetchval(
        "INSERT INTO batch_jobs(user_id, project_id, tier, max_output_tokens, total_items, status) "
        "VALUES ($1, $2, $3, $4, $5, 'pending') RETURNING id",
        user_id,
        project_id,
        tier,
        max_output_tokens,
        len(items),
    )
    for idx, item in enumerate(items):
        await pool.execute(
            "INSERT INTO batch_job_items(job_id, idx, prompt, system_prompt) VALUES ($1, $2, $3, $4)",
            job_id,
            idx,
            item["prompt"],
            item.get("system"),
        )
    return str(job_id)


async def process_job(pool, redis, job_id: str) -> None:
    job = await pool.fetchrow("SELECT * FROM batch_jobs WHERE id = $1", job_id)
    if not job:
        log.warning("batch job %s no encontrado", job_id)
        return
    if job["status"] not in ("pending", "running"):
        return

    await pool.execute(
        "UPDATE batch_jobs SET status = 'running', started_at = COALESCE(started_at, now()) WHERE id = $1",
        job_id,
    )
    project = await pool.fetchrow("SELECT * FROM projects WHERE id = $1", job["project_id"])
    if not project:
        await pool.execute(
            "UPDATE batch_jobs SET status = 'failed', error_message = $2, finished_at = now() WHERE id = $1",
            job_id,
            "Proyecto no encontrado",
        )
        return

    rows = await pool.fetch(
        "SELECT * FROM batch_job_items WHERE job_id = $1 AND status = 'pending' ORDER BY idx",
        job_id,
    )
    completed = int(job["completed_items"] or 0)
    failed = int(job["failed_items"] or 0)

    for row in rows:
        await pool.execute("UPDATE batch_job_items SET status = 'running' WHERE id = $1", row["id"])
        messages = []
        if row["system_prompt"]:
            messages.append({"role": "system", "content": row["system_prompt"]})
        messages.append({"role": "user", "content": row["prompt"]})
        try:
            out = await ask_exec.run_ask(
                pool,
                redis,
                project=project,
                user_id=job["user_id"],
                access_token_id=None,
                tier_requested=job["tier"],
                max_output_tokens=job["max_output_tokens"],
                messages=messages,
                prompt=row["prompt"],
            )
            await pool.execute(
                "UPDATE batch_job_items SET status = 'ok', result_text = $2, request_id = $3::uuid, "
                "latency_ms = $4, finished_at = now() WHERE id = $1",
                row["id"],
                out["text"],
                out["request_id"],
                out["latency_ms"],
            )
            completed += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "batch_item_failed", "message": str(exc.detail)}
            err_code = detail.get("code", "batch_item_failed")
            err_msg = str(detail.get("message", ""))[:500]
            await pool.execute(
                "UPDATE batch_job_items SET status = 'error', error_code = $2, error_message = $3, finished_at = now() WHERE id = $1",
                row["id"],
                err_code,
                err_msg,
            )
            failed += 1

        await pool.execute(
            "UPDATE batch_jobs SET completed_items = $2, failed_items = $3 WHERE id = $1",
            job_id,
            completed,
            failed,
        )

    total = int(job["total_items"])
    if completed + failed < total:
        final_status = "running"
    elif completed == 0:
        final_status = "failed"
    else:
        final_status = "completed"

    await pool.execute(
        "UPDATE batch_jobs SET status = $2, completed_items = $3, failed_items = $4, finished_at = now() WHERE id = $1",
        job_id,
        final_status,
        completed,
        failed,
    )
