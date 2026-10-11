from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.deps import current_user, err
from app.prompt_render import extract_variables
from app.routers.projects import get_project

router = APIRouter(prefix="/v1/projects", tags=["prompts"])


class PromptIn(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    system_prompt: str = Field(default="", max_length=50000)
    user_template: str = Field(min_length=1, max_length=200000)
    default_tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")


def _prompt_out(r, include_vars: bool = False) -> dict:
    out = {
        "id": str(r["id"]),
        "name": r["name"],
        "version": r["version"],
        "system_prompt": r["system_prompt"],
        "user_template": r["user_template"],
        "default_tier": r["default_tier"],
        "created_at": r["created_at"].isoformat(),
    }
    if include_vars:
        vars_sys = extract_variables(r["system_prompt"] or "")
        vars_user = extract_variables(r["user_template"] or "")
        out["variables"] = sorted(vars_sys | vars_user)
    return out


async def _latest_version(pool, project_id, name: str) -> int:
    return int(await pool.fetchval(
        "SELECT coalesce(max(version), 0) FROM project_prompts WHERE project_id = $1 AND name = $2",
        project_id, name,
    ) or 0)


@router.get("/{project_id}/prompts")
async def list_prompts(project_id: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    rows = await pool.fetch(
        "SELECT DISTINCT ON (name) id, name, version, system_prompt, user_template, default_tier, created_at "
        "FROM project_prompts WHERE project_id = $1 ORDER BY name, version DESC",
        project["id"],
    )
    return [_prompt_out(r, include_vars=True) for r in rows]


@router.get("/{project_id}/prompts/{name}")
async def get_prompt(
    project_id: str, name: str, request: Request, version: int | None = None,
    user: dict = Depends(current_user),
):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    if version is not None:
        row = await pool.fetchrow(
            "SELECT * FROM project_prompts WHERE project_id = $1 AND name = $2 AND version = $3",
            project["id"], name, version,
        )
    else:
        row = await pool.fetchrow(
            "SELECT * FROM project_prompts WHERE project_id = $1 AND name = $2 ORDER BY version DESC LIMIT 1",
            project["id"], name,
        )
    if not row:
        raise err(404, "not_found", "Plantilla no encontrada.")
    return _prompt_out(row, include_vars=True)


@router.get("/{project_id}/prompts/{name}/versions")
async def list_prompt_versions(project_id: str, name: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    rows = await pool.fetch(
        "SELECT id, name, version, system_prompt, user_template, default_tier, created_at "
        "FROM project_prompts WHERE project_id = $1 AND name = $2 ORDER BY version DESC",
        project["id"], name,
    )
    if not rows:
        raise err(404, "not_found", "Plantilla no encontrada.")
    return [_prompt_out(r) for r in rows]


@router.post("/{project_id}/prompts", status_code=201)
async def upsert_prompt(project_id: str, body: PromptIn, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    name = body.name.strip()
    next_ver = await _latest_version(pool, project["id"], name) + 1
    try:
        row = await pool.fetchrow(
            "INSERT INTO project_prompts(project_id, name, version, system_prompt, user_template, default_tier) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
            project["id"], name, next_ver, body.system_prompt.strip(), body.user_template.strip(), body.default_tier,
        )
    except asyncpg.UniqueViolationError:
        raise err(409, "prompt_version_conflict", "Conflicto de versión; reintenta.") from None
    return _prompt_out(row, include_vars=True)


@router.delete("/{project_id}/prompts/{name}")
async def delete_prompt(project_id: str, name: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    res = await pool.execute("DELETE FROM project_prompts WHERE project_id = $1 AND name = $2", project["id"], name)
    if res.endswith(" 0"):
        raise err(404, "not_found", "Plantilla no encontrada.")
    return {"ok": True}
