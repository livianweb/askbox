from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.deps import current_user, err
from app.project_auth import new_access_token, parse_scopes
from app.routers.projects import get_project, parse_uuid
from app.security import token_hash

router = APIRouter(prefix="/v1/projects", tags=["access-tokens"])


class AccessTokenIn(BaseModel):
    label: str = Field(default="", max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=8)


def _token_out(r) -> dict:
    revoked = r.get("revoked_at")
    return {
        "id": str(r["id"]),
        "label": r["label"],
        "prefix": r["prefix"],
        "last4": r.get("last4"),
        "external_user_id": r.get("external_user_id"),
        "scopes": list(r["scopes"] or []),
        "created_at": r["created_at"].isoformat(),
        "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
        "revoked_at": revoked.isoformat() if revoked else None,
    }


@router.get("/{project_id}/access-tokens")
async def list_access_tokens(project_id: str, request: Request, user: dict = Depends(current_user)):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    rows = await pool.fetch(
        "SELECT id, label, prefix, last4, external_user_id, scopes, created_at, last_used_at, revoked_at "
        "FROM project_access_tokens WHERE project_id = $1 ORDER BY created_at DESC",
        project["id"],
    )
    return [_token_out(r) for r in rows]


@router.post("/{project_id}/access-tokens", status_code=201)
async def create_access_token(
    project_id: str, body: AccessTokenIn, request: Request, user: dict = Depends(current_user),
):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    scopes = parse_scopes(body.scopes)
    token, prefix = new_access_token()
    row = await pool.fetchrow(
        "INSERT INTO project_access_tokens(project_id, label, token_hash, prefix, scopes, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "RETURNING id, label, prefix, last4, scopes, created_at, last_used_at",
        project["id"], body.label.strip(), token_hash(token), prefix, scopes, token[-4:],
    )
    out = _token_out(row)
    out["token"] = token
    out["note"] = "Guárdalo ahora; no se puede volver a consultar."
    return out


@router.delete("/{project_id}/access-tokens/{token_id}")
async def revoke_access_token(
    project_id: str, token_id: str, request: Request, user: dict = Depends(current_user),
):
    pool = request.app.state.pool
    project = await get_project(pool, user["id"], project_id)
    res = await pool.execute(
        "UPDATE project_access_tokens SET revoked_at = $3 "
        "WHERE id = $1 AND project_id = $2 AND revoked_at IS NULL",
        parse_uuid(token_id), project["id"], datetime.now(timezone.utc),
    )
    if res.endswith(" 0"):
        raise err(404, "not_found", "Token no encontrado.")
    return {"ok": True}
