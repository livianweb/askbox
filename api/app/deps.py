"""Dependencias FastAPI: pool, redis y usuario autenticado (cookie o Bearer)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import settings
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
