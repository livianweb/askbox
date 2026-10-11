"""Dependencias FastAPI: pool, redis y usuario autenticado (cookie o Bearer)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

from app.config import settings
from app.project_auth import has_scope, lookup_access_token
from app.security import token_hash


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


async def current_user(request: Request) -> dict:
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get(settings.cookie_name)
    if not token:
        raise err(401, "unauthenticated", "Autenticación requerida (cookie de sesión o Bearer).")
    pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT u.id, u.email, s.id AS session_id FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = $1 AND s.expires_at > now()",
        token_hash(token),
    )
    if not row:
        raise err(401, "unauthenticated", "Sesión inválida o caducada.")
    await pool.execute("UPDATE sessions SET last_seen_at = now() WHERE id = $1 AND last_seen_at < now() - interval '5 minutes'", row["session_id"])
    return {"id": row["id"], "email": row["email"], "session_id": row["session_id"]}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


@dataclass
class AskAuth:
    mode: Literal["session", "access_token"]
    user_id: object
    project_id: object | None
    access_token_id: object | None
    scopes: list[str]


async def ask_auth(request: Request) -> AskAuth:
    """Sesión de usuario o Bearer ab_live_ con scope ask:read (proyecto fijado por el token)."""
    pool = request.app.state.pool
    bearer = _bearer_token(request)
    if bearer and bearer.startswith("ab_live_"):
        ctx = await lookup_access_token(pool, bearer)
        if not ctx:
            raise err(401, "unauthenticated", "Token de proyecto inválido o revocado.")
        if not has_scope(ctx["scopes"], "ask:read"):
            raise err(403, "forbidden", "El token no tiene scope ask:read.")
        return AskAuth(
            mode="access_token",
            user_id=ctx["user_id"],
            project_id=ctx["project_id"],
            access_token_id=ctx["access_token_id"],
            scopes=ctx["scopes"],
        )
    user = await current_user(request)
    return AskAuth(
        mode="session",
        user_id=user["id"],
        project_id=None,
        access_token_id=None,
        scopes=[],
    )
