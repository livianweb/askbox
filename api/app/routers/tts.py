"""POST /v1/tts — texto a voz (Piper, local). Separado de /v1/ask: aquí no hay LLM.

Auth (cualquiera de):
- `Authorization: Bearer <ASKBOX_TTS_SERVICE_KEY>`: clave de servicio para backends propios
  (p. ej. Snake OS hace de proxy same-origin; la clave nunca llega al navegador).
- `Authorization: Bearer ab_live_…` con scope `tts:synth`.
- Cookie de sesión de askbox.
Sin CORS: pensado para llamadas servidor a servidor.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.deps import _bearer_token, client_ip, current_user, err
from app.project_auth import has_scope, lookup_access_token
from app.tts import engine

log = logging.getLogger("askbox.tts")

router = APIRouter(prefix="/v1", tags=["tts"])

PER_MIN = int(os.environ.get("ASKBOX_TTS_RATE_PER_MIN", "120") or 120)
_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _service_key() -> str:
    return (os.environ.get("ASKBOX_TTS_SERVICE_KEY") or "").strip()


async def tts_auth(request: Request) -> str:
    """Devuelve un identificador del llamante para el límite de ritmo."""
    bearer = _bearer_token(request)
    sk = _service_key()
    if bearer and sk and hmac.compare_digest(bearer.encode(), sk.encode()):
        return "svc"
    if bearer and bearer.startswith("ab_live_"):
        ctx = await lookup_access_token(request.app.state.pool, bearer)
        if not ctx:
            raise err(401, "unauthenticated", "Token de proyecto inválido o revocado.")
        if not has_scope(ctx["scopes"], "tts:synth"):
            raise err(403, "forbidden", "El token no tiene scope tts:synth.")
        return f"tok:{ctx['access_token_id']}"
    user = await current_user(request)
    return f"u:{user['id']}"


def _check_rate(who: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        calls = [t for t in _rate.get(who, []) if t > now - 60]
        if len(calls) >= PER_MIN:
            raise err(429, "rate_limited", "Demasiadas peticiones de voz; espera un momento.")
        calls.append(now)
        _rate[who] = calls
        if len(_rate) > 5000:
            _rate.clear()


class TtsBody(BaseModel):
    text: str = Field(..., max_length=4000, description="Texto a locutar (se recorta a ASKBOX_TTS_MAX_CHARS).")
    lang: str = Field("es", max_length=8, description="es | en")
    style: str = Field("plain", max_length=16, description="plain | snake (timbre propio de Snake OS)")


@router.get("/tts/status", summary="Estado del motor TTS (voces cargadas)")
async def tts_status():
    return engine.status()


@router.post(
    "/tts",
    summary="Texto a voz (WAV)",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}, "description": "WAV mono 16 bit"}},
)
async def tts(body: TtsBody, request: Request, who: str = Depends(tts_auth)):
    text = engine.clean(body.text)
    if not text:
        raise err(422, "empty_text", "Texto vacío.")
    style = body.style if body.style in engine.STYLES else "plain"
    lang = engine.norm_lang(body.lang)
    _check_rate(who if who != "svc" else "svc:" + client_ip(request))
    t0 = time.monotonic()
    try:
        data = await asyncio.to_thread(engine.synth, lang, text, style)
    except Exception as exc:
        log.warning("tts: fallo %s: %s", type(exc).__name__, str(exc)[:160])
        raise err(503, "tts_unavailable", "Motor de voz no disponible.")
    ms = int((time.monotonic() - t0) * 1000)
    return Response(
        content=data,
        media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=3600", "X-TTS-Ms": str(ms)},
    )
