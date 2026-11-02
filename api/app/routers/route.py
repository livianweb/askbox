"""POST /v1/route — controlador de contexto para el 7B local (sanitize / OpenClaw).

sanitize envía señales cortas, no el hilo completo. Un modelo de nube del tier
`fast` (Gemini u OpenRouter, mismas claves que `/v1/ask`) elige un perfil fijo.
sanitize aplica `keep_messages`, `system` y `max_out`. Si la nube no responde
dentro del presupuesto, la respuesta es el perfil lean (HTTP 200).

Auth (`Authorization: Bearer …`):
- Token de proyecto `ab_live_…` con scope `ask:read` (el valor que sanitize
  guarda como ASKBOX_TOKEN). Misma resolución de proyecto que `/v1/ask`.
- O secreto de servicio: variable de entorno `ASKBOX_TOKEN` (comparación en
  tiempo constante). No uses esta vía con un `ab_live_…`. Las claves de nube
  salen de `ASKBOX_ROUTE_PROJECT_ID` o, si falta, del proyecto con
  `allow_system_keys` o con una clave activa. No hay credenciales nuevas:
  `GEMINI_API_KEY` / `OPENROUTER_API_KEY` o claves del proyecto.

Env:
- `ASKBOX_TOKEN` — secreto de servicio opcional. Vacío = solo `ab_live_…` o sesión.
- `ASKBOX_ROUTE_PROJECT_ID` — uuid de proyecto para el secreto de servicio.
- `ASKBOX_ROUTE_TIMEOUT_MS` — presupuesto de la llamada a nube (default 500, tope 650;
  sanitize corta ~700 ms y también cae a lean).
- `ASKBOX_ROUTE_RATE_PER_MIN` — default 120. Si se supera, HTTP 200 con perfil lean
  y `reason=rate_limited` (el turno local no se bloquea). 0 desactiva el límite.

Ejemplo:

    POST /v1/route
    {"last_user":"arregla el test","n_messages":9,"approx_chars":4200,
     "has_code":true,"has_tools":false,"profiles":["lean","standard","full"]}

    {"profile":"full","keep_messages":12,"system":"full","max_out":256,"reason":"code task"}

Timeout:

    {"profile":"lean","keep_messages":2,"system":"lean","max_out":96,"reason":"timeout"}
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app import ratelimit
from app.config import settings
from app.deps import _bearer_token, ask_auth, err
from app.llm import router as llm_router
from app.llm.local_route import (
    PROFILE_IDS,
    ROUTE_SCHEMA,
    build_messages,
    fallback_id,
    is_fallback_reason,
    pack,
    parse_choice,
)
from app.llm.router import AskFailure

log = logging.getLogger("askbox.route")

router = APIRouter(prefix="/v1", tags=["route"])

_TIMEOUT_CAP_MS = 650.0
_TIMEOUT_FLOOR_MS = 50.0


class RouteIn(BaseModel):
    last_user: str = Field(..., max_length=20000)
    n_messages: int = Field(..., ge=0, le=10000)
    approx_chars: int = Field(..., ge=0, le=2_000_000)
    has_code: bool
    has_tools: bool
    profiles: list[str] = Field(default_factory=lambda: list(PROFILE_IDS))

    @field_validator("profiles")
    @classmethod
    def _known_profiles(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("profiles vacío")
        out: list[str] = []
        for raw in value:
            name = str(raw).strip().lower()
            if name not in PROFILE_IDS:
                raise ValueError("profile desconocido")
            if name not in out:
                out.append(name)
        return out


class RouteOut(BaseModel):
    profile: Literal["lean", "standard", "full"]
    keep_messages: int
    system: Literal["lean", "full", "summary"]
    max_out: int
    reason: str


def service_token() -> str:
    return (os.getenv("ASKBOX_TOKEN") or "").strip()


def route_timeout_s() -> float:
    raw = (os.getenv("ASKBOX_ROUTE_TIMEOUT_MS") or "500").strip()
    try:
        ms = float(raw)
    except ValueError:
        ms = 500.0
    ms = min(max(ms, _TIMEOUT_FLOOR_MS), _TIMEOUT_CAP_MS)
    return ms / 1000.0


def route_rate_per_min() -> int:
    raw = (os.getenv("ASKBOX_ROUTE_RATE_PER_MIN") or "120").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 120


def _route_project_id() -> str | None:
    raw = (os.getenv("ASKBOX_ROUTE_PROJECT_ID") or "").strip()
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        log.warning("ASKBOX_ROUTE_PROJECT_ID inválido")
        return None


async def route_caller(request: Request) -> dict[str, Any]:
    """Servicio (`ASKBOX_TOKEN`) o la misma auth que `/v1/ask`."""
    bearer = _bearer_token(request)
    token = service_token()
    if (
        bearer
        and token
        and not bearer.startswith("ab_live_")
        and hmac.compare_digest(bearer.encode(), token.encode())
    ):
        return {"mode": "service", "user_id": None, "project_id": None, "access_token_id": None}
    if not bearer and not request.cookies.get(settings.cookie_name):
        raise err(401, "unauthenticated", "Autenticación requerida (Bearer ASKBOX_TOKEN).")
    if bearer and not bearer.startswith("ab_live_"):
        raise err(401, "unauthenticated", "Token inválido.")
    auth = await ask_auth(request)
    return {
        "mode": auth.mode,
        "user_id": auth.user_id,
        "project_id": auth.project_id,
        "access_token_id": auth.access_token_id,
    }


async def load_project(pool, caller: dict[str, Any]):
    if pool is None:
        return None
    mode = caller.get("mode")
    if mode == "access_token" and caller.get("project_id"):
        return await pool.fetchrow("SELECT * FROM projects WHERE id = $1", caller["project_id"])
    if mode == "session" and caller.get("user_id"):
        return await pool.fetchrow(
            "SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at LIMIT 1",
            caller["user_id"],
        )
    pid = _route_project_id()
    if pid:
        row = await pool.fetchrow("SELECT * FROM projects WHERE id = $1::uuid", pid)
        if row:
            return row
    return await pool.fetchrow(
        "SELECT p.* FROM projects p "
        "LEFT JOIN api_keys k ON k.project_id = p.id AND k.status = 'active' "
        "GROUP BY p.id "
        "ORDER BY p.allow_system_keys DESC, COUNT(k.id) DESC, p.created_at "
        "LIMIT 1"
    )


async def cloud_text(pool, redis, project, messages: list[dict]) -> str:
    """Tier fast del catálogo. Sin Ollama y sin allin."""
    res = await llm_router.ask(
        pool,
        redis,
        project=dict(project),
        messages=messages,
        tier="fast",
        max_tokens=64,
        json_schema=ROUTE_SCHEMA,
        temperature=0.0,
    )
    return str(res.get("text") or "")


async def choose_profile(signals: dict[str, Any], cloud, timeout_s: float) -> dict[str, Any]:
    allowed = list(signals.get("profiles") or PROFILE_IDS)
    safe = fallback_id(allowed)
    messages = build_messages(signals, allowed)

    async def _call() -> str:
        return await cloud(messages)

    try:
        text = await asyncio.wait_for(_call(), timeout_s)
    except TimeoutError:
        return pack(safe, "timeout")
    except (AskFailure, Exception) as exc:  # noqa: BLE001
        log.info("route nube no disponible: %s", type(exc).__name__)
        return pack(safe, "cloud_unavailable")
    parsed = parse_choice(text, allowed)
    if not parsed:
        return pack(safe, "invalid_profile")
    profile, reason = parsed
    return pack(profile, reason)


def _bucket(caller: dict[str, Any]) -> str:
    if caller.get("mode") == "service":
        return "route:svc"
    if caller.get("access_token_id"):
        return f"route:token:{caller['access_token_id']}"
    return f"route:user:{caller.get('user_id')}"


@router.post(
    "/route",
    response_model=RouteOut,
    summary="Elegir perfil de contexto para el modelo local",
    description=(
        "Auth: `Authorization: Bearer` con el token de proyecto (`ab_live_…`, scope ask:read) "
        "o con el secreto `ASKBOX_TOKEN`. "
        "Cuerpo: last_user, n_messages, approx_chars, has_code, has_tools, profiles. "
        "Respuesta: profile (lean|standard|full) y la tupla fija keep_messages, system, max_out, reason. "
        "Timeout, nube caída o límite de ritmo: HTTP 200 con el perfil más corto permitido "
        "(lean si está en la lista)."
    ),
)
async def route_turn(body: RouteIn, request: Request):
    caller = await route_caller(request)
    signals = body.model_dump()
    try:
        redis = request.app.state.redis
    except AttributeError:
        redis = None
    try:
        await ratelimit.check(redis, _bucket(caller), route_rate_per_min())
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        if status == 429:
            allowed = list(body.profiles)
            return pack(fallback_id(allowed), "rate_limited")
        raise
    try:
        pool = request.app.state.pool
    except AttributeError:
        pool = None
    try:
        project = await load_project(pool, caller)
    except Exception as exc:  # noqa: BLE001
        log.info("route proyecto no disponible: %s", type(exc).__name__)
        project = None

    async def cloud(messages: list[dict]) -> str:
        if project is None:
            raise RuntimeError("no_project")
        return await cloud_text(pool, redis, project, messages)

    decision = await choose_profile(signals, cloud, route_timeout_s())
    source = decision["reason"] if is_fallback_reason(decision["reason"]) else "cloud"
    log.info("route profile=%s source=%s", decision["profile"], source)
    return decision
