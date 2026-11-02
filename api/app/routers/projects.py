from __future__ import annotations

import json
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.crypto import CryptoError, encrypt_secret, master_key_ready
from app.deps import current_user, err
from app.llm.local_models import LocalPolicyError, apply_update, public_view

router = APIRouter(prefix="/v1/projects", tags=["projects"])
PROVIDERS = {"gemini", "openrouter"}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    default_tier: str = Field(default="auto", pattern="^(auto|fast|balanced|strong)$")
    allow_system_keys: bool = False
    cache_ttl_seconds: int = Field(default=0, ge=0, le=86400)


class KeyIn(BaseModel):
    provider: str = Field(pattern="^(gemini|openrouter)$")
    label: str = Field(default="", max_length=80)
    key: str = Field(min_length=8, max_length=512)


class OllamaModelIn(BaseModel):
    slug: str = Field(min_length=1, max_length=128)
    enabled: bool


class OllamaModelsIn(BaseModel):
    primary: str | None = Field(default=None, max_length=128)
    models: list[OllamaModelIn] = Field(default_factory=list)


def _project_out(r) -> dict:
    return {
        "id": str(r["id"]), "name": r["name"], "default_tier": r["default_tier"],
        "allow_system_keys": r["allow_system_keys"], "cache_ttl_seconds": r["cache_ttl_seconds"],
        "created_at": r["created_at"].isoformat(),
    }


def _key_out(r) -> dict:
    # Solo metadatos seguros: nunca ciphertext/nonce/plaintext.
    return {
        "id": str(r["id"]), "provider": r["provider"], "label": r["label"], "last4": r["last4"],
        "status": r["status"], "cooldown_until": r["cooldown_until"].isoformat() if r["cooldown_until"] else None,
        "created_at": r["created_at"].isoformat(),
        "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
    }


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise err(404, "not_found", "Recurso no encontrado.") from None


async def get_project(pool, user_id, project_id: str):
    row = await pool.fetchrow("SELECT * FROM projects WHERE id = $1 AND user_id = $2", parse_uuid(project_id), user_id)
    if not row:
        raise err(404, "not_found", "Proyecto no encontrado.")
    return row


@router.get("")
async def list_projects(request: Request, user: dict = Depends(current_user)):
    rows = await request.app.state.pool.fetch("SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at", user["id"])
    return [_project_out(r) for r in rows]


@router.post("", status_code=201)
async def create_project(body: ProjectIn, request: Request, user: dict = Depends(current_user)):
    try:
        row = await request.app.state.pool.fetchrow(
            "INSERT INTO projects(user_id, name, default_tier, allow_system_keys, cache_ttl_seconds) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            user["id"], body.name.strip(), body.default_tier, body.allow_system_keys, body.cache_ttl_seconds,
        )
    except asyncpg.UniqueViolationError:
        raise err(409, "project_exists", "Ya existe un proyecto con ese nombre.") from None
    return _project_out(row)


@router.get("/{project_id}/ollama-models")
async def get_ollama_models(project_id: str, request: Request, user: dict = Depends(current_user)):
    """Modelos locales activos para el enrutado automático de este proyecto."""
    project = await get_project(request.app.state.pool, user["id"], project_id)
    return public_view(project)


@router.put("/{project_id}/ollama-models")
async def put_ollama_models(
    project_id: str,
    body: OllamaModelsIn,
    request: Request,
    user: dict = Depends(current_user),
):
    """Activa o desactiva slots locales. No borra modelos de Ollama."""
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    try:
        stored = apply_update(
            project,
            [item.model_dump() for item in body.models],
            body.primary,
            primary_set="primary" in body.model_fields_set,
        )
    except LocalPolicyError as exc:
        raise err(422, exc.code, str(exc)) from None
    row = await pool.fetchrow(
        "UPDATE projects SET ollama_models = $1::jsonb WHERE id = $2 RETURNING *",
        json.dumps(stored),
        project["id"],
    )
    return public_view(row)


@router.get("/{project_id}/keys")
async def list_keys(project_id: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    rows = await pool.fetch(
        "SELECT id, provider, label, last4, status, cooldown_until, created_at, last_used_at "
        "FROM api_keys WHERE project_id = $1 ORDER BY created_at", project["id"],
    )
    return [_key_out(r) for r in rows]


@router.post("/{project_id}/keys", status_code=201)
async def add_key(project_id: str, body: KeyIn, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    secret = body.key.strip()
    if len(secret) < 8 or any(c.isspace() for c in secret):
        raise err(422, "invalid_key", "Formato de clave no válido.")
    if not master_key_ready():
        raise err(503, "master_key_missing", "ASKBOX_MASTER_KEY no está configurada en el servidor.")
    try:
        ct, nonce, version = encrypt_secret(secret, str(project["id"]), body.provider)
    except CryptoError:
        raise err(503, "master_key_invalid", "ASKBOX_MASTER_KEY no es válida.") from None
    row = await pool.fetchrow(
        "INSERT INTO api_keys(project_id, provider, label, ciphertext, nonce, key_version, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "RETURNING id, provider, label, last4, status, cooldown_until, created_at, last_used_at",
        project["id"], body.provider, body.label.strip(), ct, nonce, version, secret[-4:],
    )
    return _key_out(row)


@router.delete("/{project_id}/keys/{key_id}")
async def delete_key(project_id: str, key_id: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    res = await pool.execute("DELETE FROM api_keys WHERE id = $1 AND project_id = $2", parse_uuid(key_id), project["id"])
    if res.endswith(" 0"):
        raise err(404, "not_found", "Clave no encontrada.")
    return {"ok": True}
