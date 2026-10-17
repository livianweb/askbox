"""Tokens ab_live_ emitidos por un backend de confianza para usuarios externos.

Auth: Bearer ab_live_ con scope tokens:write, ligado al proyecto del token.
El secreto se devuelve una sola vez. Listar solo muestra prefix y last4.
Estos tokens no pueden leer claves de proveedor ni emitir otros tokens de servicio.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.deps import err, service_token_auth
from app.project_auth import new_access_token, parse_delegated_scopes, parse_external_user_id
from app.routers.projects import parse_uuid
from app.security import token_hash

router = APIRouter(prefix="/v1/projects", tags=["external-tokens"])


class ExternalTokenIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=80)
    scopes: list[str] | None = Field(default=None, max_length=2)


def _same_project(ctx: dict, project_id: str) -> uuid.UUID:
    pid = parse_uuid(project_id)
    token_pid = ctx["project_id"]
    if not isinstance(token_pid, uuid.UUID):
        try:
            token_pid = uuid.UUID(str(token_pid))
        except ValueError:
            raise err(403, "forbidden", "El token de servicio no pertenece a este proyecto.") from None
    if token_pid != pid:
        raise err(403, "forbidden", "El token de servicio no pertenece a este proyecto.")
    return pid


def _public(r) -> dict:
    revoked = r.get("revoked_at")
    return {
        "id": str(r["id"]),
        "external_user_id": r["external_user_id"],
        "label": r["label"],
        "prefix": r["prefix"],
        "last4": r.get("last4"),
        "scopes": list(r["scopes"] or []),
        "created_at": r["created_at"].isoformat(),
        "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
        "revoked_at": revoked.isoformat() if revoked else None,
    }


@router.post("/{project_id}/external-tokens", status_code=201)
async def create_external_token(
    project_id: str,
    body: ExternalTokenIn,
    request: Request,
    ctx: dict = Depends(service_token_auth),
):
    """Emite un ab_live_ para un usuario externo. El campo token solo sale en esta respuesta."""
    pool = request.app.state.pool
    pid = _same_project(ctx, project_id)
    external_user_id = parse_external_user_id(body.external_user_id)
    scopes = parse_delegated_scopes(body.scopes)
    token, prefix = new_access_token()
    row = await pool.fetchrow(
        "INSERT INTO project_access_tokens"
        "(project_id, label, token_hash, prefix, scopes, external_user_id, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "RETURNING id, label, prefix, last4, scopes, external_user_id, created_at, last_used_at",
        pid,
        body.label.strip(),
        token_hash(token),
        prefix,
        scopes,
        external_user_id,
        token[-4:],
    )
    out = _public(row)
    out["token"] = token
    out["note"] = "Guárdalo ahora; no se puede volver a consultar."
    return out


@router.get("/{project_id}/external-tokens")
async def list_external_tokens(
    project_id: str,
    request: Request,
    external_user_id: str | None = Query(default=None, max_length=128),
    ctx: dict = Depends(service_token_auth),
):
    """Metadatos (prefix, last4, scopes). Nunca el secreto ni el hash."""
    pool = request.app.state.pool
    pid = _same_project(ctx, project_id)
    if external_user_id is not None and external_user_id.strip():
        ext = parse_external_user_id(external_user_id)
        rows = await pool.fetch(
            "SELECT id, label, prefix, last4, scopes, external_user_id, created_at, last_used_at, revoked_at "
            "FROM project_access_tokens "
            "WHERE project_id = $1 AND external_user_id = $2 "
            "ORDER BY created_at DESC",
            pid,
            ext,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, label, prefix, last4, scopes, external_user_id, created_at, last_used_at, revoked_at "
            "FROM project_access_tokens "
            "WHERE project_id = $1 AND external_user_id IS NOT NULL "
            "ORDER BY created_at DESC",
            pid,
        )
    return [_public(r) for r in rows]


@router.delete("/{project_id}/external-tokens/{token_id}")
async def revoke_external_token(
    project_id: str,
    token_id: str,
    request: Request,
    ctx: dict = Depends(service_token_auth),
):
    """Revoca un token de usuario externo. No puede revocar el token de servicio."""
    pool = request.app.state.pool
    pid = _same_project(ctx, project_id)
    res = await pool.execute(
        "UPDATE project_access_tokens SET revoked_at = $3 "
        "WHERE id = $1 AND project_id = $2 AND external_user_id IS NOT NULL AND revoked_at IS NULL",
        parse_uuid(token_id),
        pid,
        datetime.now(timezone.utc),
    )
    if res.endswith(" 0"):
        raise err(404, "not_found", "Token no encontrado.")
    return {"ok": True}
