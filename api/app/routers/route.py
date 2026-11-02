"""POST /v1/route — controlador de contexto para el 7B local (sanitize / OpenClaw).

sanitize envía señales cortas, no el hilo completo. Un modelo de nube del tier
`fast` elige un perfil fijo. La primera llamada es gemini-flash-lite-latest
(clave del proyecto; GEMINI_API_KEY vacío no importa si hay clave en BD).
2.0 y 8b no se usan. OpenRouter no entra. Si la nube no responde a tiempo,
la respuesta es standard con keep=4 y system=lean. La llamada en frío puede
seguir en segundo plano (~15–20 s) para que la siguiente, ya caliente, quepa
en el presupuesto.

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
- `ASKBOX_ROUTE_TIMEOUT_MS` — presupuesto de la llamada a Gemini (default 650, tope 780;
  sanitize corta ~800 ms).
- `ASKBOX_ROUTE_RATE_PER_MIN` — default 120. Si se supera, HTTP 200 con perfil standard
  y `reason=rate_limited`. 0 desactiva el límite.

Ejemplo:

    POST /v1/route
    {"last_user":"arregla el test","n_messages":9,"approx_chars":4200,
     "has_code":true,"has_tools":false,"profiles":["lean","standard","full"]}

    {"profile":"full","keep_messages":12,"system":"full","max_out":256,"reason":"code task"}

Timeout:

    {"profile":"standard","keep_messages":4,"system":"lean","max_out":128,"reason":"timeout"}
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app import ratelimit
from app.config import settings
from app.deps import _bearer_token, ask_auth, err
from app.llm import keypool
from app.llm.errors import LLMError
from app.llm.local_route import (
    PROFILE_IDS,
    ROUTE_MAX_TOKENS,
    build_messages,
    fallback_id,
    is_fallback_reason,
    order_route_models,
    pack,
    parse_choice,
)
from app.llm.providers import gemini
from app.project_auth import has_scope
from app.security import token_hash

log = logging.getLogger("askbox.route")

router = APIRouter(prefix="/v1", tags=["route"])

_TIMEOUT_CAP_MS = 780.0
_TIMEOUT_FLOOR_MS = 50.0
# slug + clave de Gemini, para no repetir catálogo y keypool en cada turno.
_HOT: dict[str, tuple[float, str, str]] = {}
_HOT_TTL_S = 30.0
# generateContent 404: el id no existe para esta clave. No se reintenta.
_MISSING: set[str] = set()
_MAX_MODEL_TRIES = 4
# La primera generateContent puede tardar 15–20 s. No se cancela: calienta el modelo.
_inflight: asyncio.Task | None = None


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
    raw = (os.getenv("ASKBOX_ROUTE_TIMEOUT_MS") or "650").strip()
    try:
        ms = float(raw)
    except ValueError:
        ms = 650.0
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
        return {"mode": "service", "user_id": None, "project_id": None, "access_token_id": None, "project": None}
    if not bearer and not request.cookies.get(settings.cookie_name):
        raise err(401, "unauthenticated", "Autenticación requerida (Bearer ASKBOX_TOKEN).")
    if bearer and not bearer.startswith("ab_live_"):
        raise err(401, "unauthenticated", "Token inválido.")
    if bearer and bearer.startswith("ab_live_"):
        # Una query: token + proyecto. El hot path no espera un segundo SELECT.
        try:
            pool = request.app.state.pool
        except AttributeError:
            pool = None
        row = None
        if pool is not None:
            row = await pool.fetchrow(
                "SELECT t.id, t.project_id, t.scopes, p.user_id, p.allow_system_keys, "
                "p.default_tier, p.cache_ttl_seconds "
                "FROM project_access_tokens t JOIN projects p ON p.id = t.project_id "
                "WHERE t.token_hash = $1 AND t.revoked_at IS NULL",
                token_hash(bearer),
            )
        if not row:
            raise err(401, "unauthenticated", "Token de proyecto inválido o revocado.")
        scopes = list(row["scopes"] or [])
        if not has_scope(scopes, "ask:read"):
            raise err(403, "forbidden", "El token no tiene scope ask:read.")
        return {
            "mode": "access_token",
            "user_id": row["user_id"],
            "project_id": row["project_id"],
            "access_token_id": row["id"],
            "project": {
                "id": row["project_id"],
                "user_id": row["user_id"],
                "allow_system_keys": row["allow_system_keys"],
                "default_tier": row["default_tier"],
                "cache_ttl_seconds": row["cache_ttl_seconds"],
            },
        }
    auth = await ask_auth(request)
    return {
        "mode": auth.mode,
        "user_id": auth.user_id,
        "project_id": auth.project_id,
        "access_token_id": auth.access_token_id,
        "project": None,
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


def _missing_model(exc: LLMError) -> bool:
    if exc.status == 404:
        return True
    text = str(exc).lower()
    return "not found" in text or "not supported" in text


def _fallback_decision(profile: str, reason: str) -> dict[str, Any]:
    """Timeout o nube caída: keep=4 (standard) pero system=lean, como sanitize."""
    out = pack(profile, reason)
    if reason in {"timeout", "cloud_unavailable"} and out["profile"] == "standard":
        out["system"] = "lean"
    return out


async def _gemini_secret(pool, project) -> str:
    keys = await keypool.candidates(pool, project, "gemini")
    if not keys:
        log.info("route sin clave gemini")
        raise RuntimeError("no_gemini")
    return keys[0].secret


async def _candidate_slugs(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT slug FROM model_catalog WHERE enabled AND provider = 'gemini'"
    )
    return [slug for slug in order_route_models([r["slug"] for r in rows]) if slug not in _MISSING]


async def cloud_text(pool, redis, project, messages: list[dict], attempt: dict | None = None) -> str:
    """Prueba gemini-flash-lite-latest y luego el lite versionado. Un 404 no se cachea."""
    del redis
    pid = str(project["id"])
    now = time.monotonic()
    secret: str | None = None
    hit = _HOT.get(pid)
    if hit and hit[0] > now and hit[1] not in _MISSING:
        secret = hit[2]
        slug = hit[1]
        if attempt is not None:
            attempt["model"] = slug
        try:
            res = await gemini.complete(
                messages,
                api_key=secret,
                model=slug,
                max_tokens=ROUTE_MAX_TOKENS,
                temperature=0.0,
                fast=True,
                timeout=25.0,
            )
            return str(res.get("text") or "")
        except LLMError as exc:
            if not _missing_model(exc):
                raise
            _MISSING.add(slug)
            _HOT.pop(pid, None)
            log.info("route gemini 404 model=%s", slug)

    if secret is None:
        secret = await _gemini_secret(pool, project)
    tried = 0
    for slug in await _candidate_slugs(pool):
        if tried >= _MAX_MODEL_TRIES:
            break
        tried += 1
        if attempt is not None:
            attempt["model"] = slug
        try:
            res = await gemini.complete(
                messages,
                api_key=secret,
                model=slug,
                max_tokens=ROUTE_MAX_TOKENS,
                temperature=0.0,
                fast=True,
                timeout=25.0,
            )
        except LLMError as exc:
            if not _missing_model(exc):
                raise
            _MISSING.add(slug)
            log.info("route gemini 404 model=%s", slug)
            continue
        _HOT[pid] = (now + _HOT_TTL_S, slug, secret)
        if len(_HOT) > 32:
            fresh = _HOT[pid]
            _HOT.clear()
            _HOT[pid] = fresh
        return str(res.get("text") or "")
    log.info("route sin modelo gemini con 200")
    raise RuntimeError("no_gemini")


async def choose_profile(
    signals: dict[str, Any],
    cloud,
    timeout_s: float,
    attempt: dict | None = None,
) -> dict[str, Any]:
    allowed = list(signals.get("profiles") or PROFILE_IDS)
    safe = fallback_id(allowed)
    messages = build_messages(signals, allowed)
    started = time.monotonic()

    async def _call() -> str:
        return await cloud(messages)

    global _inflight
    if _inflight is not None and not _inflight.done():
        log.info("route timeout model=%s latency_ms=0 detached=inflight", (attempt or {}).get("model") or "-")
        return _fallback_decision(safe, "timeout")

    task = asyncio.create_task(_call())
    done, _pending = await asyncio.wait({task}, timeout=timeout_s)
    model = (attempt or {}).get("model") or "-"
    if task not in done:
        _inflight = task

        def _clear(finished: asyncio.Task, _model: str = model) -> None:
            global _inflight
            if _inflight is finished:
                _inflight = None
            if finished.cancelled():
                return
            err = finished.exception()
            if err is not None:
                log.info("route warm end model=%s error=%s", _model, type(err).__name__)
            else:
                log.info("route warm end model=%s", _model)

        task.add_done_callback(_clear)
        elapsed = int((time.monotonic() - started) * 1000)
        log.info("route timeout model=%s latency_ms=%s detached=1", model, elapsed)
        return _fallback_decision(safe, "timeout")
    try:
        text = task.result()
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        log.info("route nube no disponible: %s model=%s latency_ms=%s", type(exc).__name__, model, elapsed)
        return _fallback_decision(safe, "cloud_unavailable")
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
        "Orden: gemini-flash-lite-latest, luego flash-lite versionado, luego gemini-flash-latest. "
        "2.0 y 8b no se llaman. Un 404 se salta. "
        "Timeout o nube caída: HTTP 200 standard con keep=4 y system=lean. "
        "La primera llamada puede tardar ~15–20 s y se deja en segundo plano; con el modelo ya caliente cabe en el presupuesto. "
        "Lean de perfil solo si la nube lo elige."
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
    project = caller.get("project")
    if project is None:
        try:
            project = await load_project(pool, caller)
        except Exception as exc:  # noqa: BLE001
            log.info("route proyecto no disponible: %s", type(exc).__name__)
            project = None
    attempt: dict[str, Any] = {"model": None}
    started = time.monotonic()

    async def cloud(messages: list[dict]) -> str:
        if project is None:
            raise RuntimeError("no_project")
        return await cloud_text(pool, redis, project, messages, attempt)

    decision = await choose_profile(signals, cloud, route_timeout_s(), attempt)
    source = decision["reason"] if is_fallback_reason(decision["reason"]) else "cloud"
    if source == "cloud":
        log.info(
            "route profile=%s source=%s model=%s latency_ms=%s",
            decision["profile"],
            source,
            attempt.get("model") or "-",
            int((time.monotonic() - started) * 1000),
        )
    return decision
