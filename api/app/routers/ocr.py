"""POST /v1/ocr — OCR genérico de una página (PDF o imagen) con Tesseract + poppler."""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import ask_exec, ratelimit
from app.config import settings
from app.deps import AskAuth, ask_auth, err
from app.ocr import engine

log = logging.getLogger("askbox.ocr")

router = APIRouter(prefix="/v1", tags=["ocr"])


class OcrIn(BaseModel):
    data_base64: str = Field(
        ...,
        description="Archivo en base64 (sin prefijo data:). No se persiste. Límite: ASKBOX_OCR_MAX_BYTES (25 MiB por defecto).",
    )
    mime: str = Field(
        ...,
        max_length=128,
        description="application/pdf, image/png, image/jpeg o image/webp. Se valida contra el contenido.",
    )
    page: int = Field(default=1, ge=1, description="Página (1-based). En imágenes siempre 1.")
    lang: str = Field(default="spa", max_length=64, description="Idioma(s) Tesseract, p. ej. `spa`, `eng`, `spa+eng`.")
    psm: int = Field(default=4, ge=1, le=13, description="Page segmentation mode de Tesseract (por defecto 4: columna de texto).")
    words: bool = Field(default=False, description="Si es true, añade `words` (texto, conf y bbox) cuando source=tesseract.")


class OcrLineOut(BaseModel):
    text: str
    segments: list[str]


class OcrBBoxOut(BaseModel):
    left: int
    top: int
    width: int
    height: int


class OcrWordOut(BaseModel):
    text: str
    conf: float
    bbox: OcrBBoxOut


class OcrOut(BaseModel):
    page: int
    page_count: int
    source: str = Field(description="pdf_text (texto nativo del PDF), tesseract (OCR) o blank (hoja en blanco).")
    rotation: int = Field(description="Grados en sentido horario aplicados antes del OCR (0, 90, 180, 270).")
    lines: list[OcrLineOut] = Field(description="Renglones visuales con segmentos separados por huecos horizontales.")
    words: list[OcrWordOut] | None = None


def _decode(b64: str) -> bytes:
    # 4 caracteres base64 = 3 bytes; el margen cubre saltos de línea y relleno.
    if len(b64) > settings.ocr_max_bytes * 4 // 3 + 4096:
        raise err(413, "file_too_large", f"Archivo demasiado grande (máx {settings.ocr_max_bytes} bytes).")
    try:
        return base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        raise err(422, "invalid_base64", "data_base64 no es base64 válido.") from None


async def _trace(pool, auth: AskAuth, *, digest: str, size: int, started: float, status: str,
                 source: str | None = None, error_code: str | None = None, chars: int | None = None) -> None:
    """Traza en `requests` (sin contenido: solo tamaño y hash truncado). Nunca rompe la petición."""
    try:
        await ask_exec.log_request(
            pool,
            user_id=auth.user_id,
            project_id=auth.project_id,
            access_token_id=auth.access_token_id,
            tier_requested="ocr",
            tier_used="ocr",
            status=status,
            provider="ocr",
            model_used=source,
            prompt_chars=size,
            prompt_hash=digest,
            response_chars=chars,
            latency_ms=int((time.monotonic() - started) * 1000),
            cache_hit=False,
            attempts=1,
            error_code=error_code,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("traza OCR no guardada: %s", type(exc).__name__)


@router.post(
    "/ocr",
    response_model=OcrOut,
    response_model_exclude_none=True,
    summary="OCR de una página (PDF o imagen)",
    description=(
        "Auth: cookie de sesión o `Authorization: Bearer ab_live_…` (scope ask:read). "
        "PDF con texto → `pdf_text` (pdftotext -layout); escaneos e imágenes → Tesseract "
        "(`tesseract`, con detección de giro y canal B de HSB); hoja en blanco → `blank`. "
        "`lines` agrupa palabras por altura en renglones con `segments`. Sin reglas de negocio. "
        "No se persiste el archivo. 413 `file_too_large`, 422 mime/idioma/página, 429 `rate_limited`, "
        "503 `ocr_busy`/`ocr_unavailable`, 504 `ocr_timeout`."
    ),
)
async def ocr(body: OcrIn, request: Request, auth: AskAuth = Depends(ask_auth)):
    pool, redis = request.app.state.pool, request.app.state.redis
    bucket = f"ocr:token:{auth.access_token_id}" if auth.access_token_id else f"ocr:{auth.user_id}"
    await ratelimit.check(redis, bucket, settings.ocr_rate_per_min)

    started = time.monotonic()
    data = _decode(body.data_base64)
    digest = hashlib.sha256(data).hexdigest()[:16]
    try:
        res = await engine.run_limited(
            engine.read_page, data, body.mime, body.page, body.lang, body.psm,
            timeout=settings.ocr_timeout,
        )
    except engine.OcrError as exc:
        await _trace(pool, auth, digest=digest, size=len(data), started=started, status="error", error_code=exc.code)
        raise err(exc.status, exc.code, exc.message) from None
    except Exception as exc:  # noqa: BLE001
        log.error("OCR falló: %s", type(exc).__name__)
        await _trace(pool, auth, digest=digest, size=len(data), started=started, status="error", error_code="ocr_failed")
        raise err(500, "ocr_failed", "La lectura OCR ha fallado.") from None

    await _trace(
        pool, auth, digest=digest, size=len(data), started=started, status="ok", source=res.source,
        chars=sum(len(line["text"]) for line in res.lines),
    )
    out: dict = {
        "page": res.page,
        "page_count": res.page_count,
        "source": res.source,
        "rotation": res.rotation,
        "lines": res.lines,
    }
    if body.words and res.source == "tesseract":
        out["words"] = [
            {"text": w.text, "conf": w.conf, "bbox": {"left": w.left, "top": w.top, "width": w.width, "height": w.height}}
            for w in res.words
        ]
    return out
