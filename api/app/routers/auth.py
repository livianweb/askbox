from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

import logging

from app import mailer, ratelimit
from app.config import settings
from app.deps import client_ip, current_user, err
from app.security import hash_password, new_token, token_hash, verify_password

log = logging.getLogger("askbox.auth")
router = APIRouter(prefix="/v1/auth", tags=["auth"])
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class Registration(Credentials):
    password_confirm: str = Field(min_length=1, max_length=256)


class ForgotBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=256)
    password_confirm: str = Field(min_length=1, max_length=256)


def _check_confirm(password: str, confirm: str) -> None:
    if not secrets.compare_digest(password.encode(), confirm.encode()):
        raise err(422, "password_mismatch", "Las contraseñas no coinciden.")


async def issue_reset_token(pool, user_id, ttl_minutes: int) -> str:
    """Crea un token de reset (solo se guarda su hash) e invalida los anteriores sin usar."""
    token = new_token()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE password_reset_tokens SET used_at = now() WHERE user_id = $1 AND used_at IS NULL", user_id
            )
            await conn.execute(
                "INSERT INTO password_reset_tokens(user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
                user_id, token_hash(token), datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
            )
    return token


async def _open_session(pool, user_id, kind: str, ttl: int) -> str:
    token = new_token()
    await pool.execute(
        "INSERT INTO sessions(user_id, token_hash, kind, expires_at) VALUES ($1, $2, $3, $4)",
        user_id, token_hash(token), kind, datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )
    return token


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.cookie_name, token, max_age=settings.session_ttl_seconds,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )


@router.post("/register", status_code=201)
async def register(body: Registration, request: Request, response: Response):
    await ratelimit.check(request.app.state.redis, f"auth:{client_ip(request)}", settings.login_rate_per_min)
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise err(422, "invalid_email", "Email no válido.")
    _check_confirm(body.password, body.password_confirm)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(7066002)")
            count = await conn.fetchval("SELECT count(*) FROM users")
            if count > 0 and not settings.allow_register:
                raise err(403, "registration_closed", "Registro cerrado (instancia mono-usuario).")
            try:
                uid = await conn.fetchval(
                    "INSERT INTO users(email, password_hash) VALUES ($1, $2) RETURNING id",
                    email, hash_password(body.password),
                )
            except asyncpg.UniqueViolationError:
                raise err(409, "email_taken", "Ese email ya está registrado.") from None
    token = await _open_session(pool, uid, "web", settings.session_ttl_seconds)
    _set_cookie(response, token)
    return {"id": str(uid), "email": email}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response):
    await ratelimit.check(request.app.state.redis, f"auth:{client_ip(request)}", settings.login_rate_per_min)
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT id, email, password_hash FROM users WHERE email = $1", body.email.strip().lower())
    if not verify_password(body.password, row["password_hash"] if row else None):
        raise err(401, "bad_credentials", "Email o contraseña incorrectos.")
    token = await _open_session(pool, row["id"], "web", settings.session_ttl_seconds)
    _set_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"]}


@router.post("/forgot")
async def forgot(body: ForgotBody, request: Request):
    """Solicita un enlace de recuperación. Respuesta siempre genérica (no revela si el email existe)."""
    redis = request.app.state.redis
    await ratelimit.check(redis, f"auth:{client_ip(request)}", settings.login_rate_per_min)
    email = body.email.strip().lower()
    await ratelimit.check(redis, f"forgot:{email}", 3)
    out: dict = {"ok": True, "message": "Si el email está registrado, recibirás un enlace para restablecer la contraseña."}
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT id FROM users WHERE email = $1", email)
    if not row:
        return out
    token = await issue_reset_token(pool, row["id"], settings.reset_ttl_minutes)
    url = f"{settings.public_url}/reset?token={token}"
    sent = await mailer.send_mail(
        email,
        "askbox: restablecer contraseña",
        f"Usa este enlace para elegir una nueva contraseña (caduca en {settings.reset_ttl_minutes} min):\n\n{url}\n\n"
        "Si no lo pediste tú, ignora este mensaje.",
    )
    if not sent and settings.log_reset_link and not mailer.smtp_configured():
        # Sin SMTP: el operador recoge el enlace en los logs del servidor (nunca va al cliente).
        log.warning("RESET_LINK email=%s url=%s", email, url)
    if settings.dev_show_reset_token:
        out["reset_url"] = url  # solo desarrollo local
    return out


@router.post("/reset")
async def reset(body: ResetBody, request: Request):
    await ratelimit.check(request.app.state.redis, f"auth:{client_ip(request)}", settings.login_rate_per_min)
    _check_confirm(body.password, body.password_confirm)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            uid = await conn.fetchval(
                "UPDATE password_reset_tokens SET used_at = now() "
                "WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now() RETURNING user_id",
                token_hash(body.token.strip()),
            )
            if not uid:
                raise err(400, "invalid_reset_token", "El enlace no es válido o ha caducado. Solicita uno nuevo.")
            await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", uid, hash_password(body.password))
            await conn.execute("UPDATE password_reset_tokens SET used_at = now() WHERE user_id = $1 AND used_at IS NULL", uid)
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", uid)  # cierra sesiones y tokens API
    return {"ok": True, "message": "Contraseña actualizada. Ya puedes entrar."}


@router.post("/logout")
async def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    await request.app.state.pool.execute("DELETE FROM sessions WHERE id = $1", user["session_id"])
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return {"id": str(user["id"]), "email": user["email"]}


@router.post("/token", status_code=201)
async def create_api_token(request: Request, user: dict = Depends(current_user)):
    """Bearer de larga duración para scripts. Se muestra una sola vez."""
    token = await _open_session(request.app.state.pool, user["id"], "api", 60 * 60 * 24 * 365)
    return {"token": token, "kind": "api", "note": "Guárdalo ahora; no se puede volver a consultar."}
