"""Tokens de acceso de proyecto (prefijo ab_live_) y scopes."""
from __future__ import annotations

import secrets

from fastapi import HTTPException

from app.security import token_hash


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})

TOKEN_PREFIX = "ab_live_"
VALID_SCOPES = frozenset({"ask:read", "prompts:write", "keys:write", "usage:read"})


def new_access_token() -> tuple[str, str]:
    """Devuelve (token en claro, prefix público para listados)."""
    secret = secrets.token_urlsafe(24)
    token = f"{TOKEN_PREFIX}{secret}"
    prefix = f"{TOKEN_PREFIX}{secret[:8]}…"
    return token, prefix


def parse_scopes(scopes: list[str]) -> list[str]:
    if not scopes:
        raise _err(422, "invalid_scopes", "Indica al menos un scope.")
    bad = [s for s in scopes if s not in VALID_SCOPES]
    if bad:
        raise _err(422, "invalid_scopes", f"Scopes no válidos: {', '.join(bad)}.")
    return sorted(set(scopes))


def has_scope(scopes: list[str] | set[str], required: str) -> bool:
    return required in scopes


async def lookup_access_token(pool, raw_token: str) -> dict | None:
    if not raw_token.startswith(TOKEN_PREFIX):
        return None
    row = await pool.fetchrow(
        "SELECT t.id, t.project_id, t.scopes, t.revoked_at, p.user_id "
        "FROM project_access_tokens t JOIN projects p ON p.id = t.project_id "
        "WHERE t.token_hash = $1 AND t.revoked_at IS NULL",
        token_hash(raw_token),
    )
    if not row:
        return None
    return {
        "access_token_id": row["id"],
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "scopes": list(row["scopes"] or []),
    }
