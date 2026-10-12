"""Adjuntos inline en POST /v1/ask: validación MIME, límites y formato neutro para proveedores."""
from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from app.deps import err

ALLOWED_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
    }
)

TASK_TRANSCRIBE = "transcribe"
TASK_EXTRACT = "extract"
TASK_INTERPRET = "interpret"
VALID_TASKS = frozenset({TASK_TRANSCRIBE, TASK_EXTRACT, TASK_INTERPRET})

_TRANSCRIBE_HINT = re.compile(
    r"\b(transcrib|transcript|literal|ocr|qué\s+dice|que\s+dice|texto\s+de\s+la\s+imagen)\b",
    re.I,
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


MAX_ATTACHMENTS = _int_env("ASKBOX_MAX_ATTACHMENTS", 8)
MAX_ATTACHMENT_BYTES = _int_env("ASKBOX_MAX_ATTACHMENT_BYTES", 5 * 1024 * 1024)
MAX_ATTACHMENTS_TOTAL_BYTES = _int_env("ASKBOX_MAX_ATTACHMENTS_TOTAL_BYTES", 15 * 1024 * 1024)


@dataclass(frozen=True)
class NormalizedAttachment:
    mime: str
    data: bytes
    sha256: str
    size: int
    filename: str | None = None


def sniff_mime(data: bytes) -> str | None:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 5 and data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def infer_task(
    prompt: str,
    *,
    json_schema: dict | None,
    task_requested: str | None,
) -> str:
    if task_requested:
        return task_requested
    if json_schema:
        return TASK_EXTRACT
    if _TRANSCRIBE_HINT.search(prompt or ""):
        return TASK_TRANSCRIBE
    return TASK_INTERPRET


def temperature_for_task(task: str) -> float:
    return 0.0 if task == TASK_TRANSCRIBE else 0.4


def attachments_cache_digest(attachments: list[NormalizedAttachment]) -> str:
    if not attachments:
        return ""
    parts = sorted(a.sha256 for a in attachments)
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def attachments_processed_meta(attachments: list[NormalizedAttachment]) -> list[dict[str, Any]]:
    return [
        {
            "mime": a.mime,
            "size_bytes": a.size,
            **({"filename": a.filename} if a.filename else {}),
        }
        for a in attachments
    ]


def attach_to_messages(messages: list[dict], attachments: list[NormalizedAttachment]) -> list[dict]:
    if not attachments:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            merged = dict(out[i])
            merged["attachments"] = attachments
            out[i] = merged
            return out
    raise err(422, "empty_prompt", "El prompt no puede estar vacío.")


def normalize_attachments(raw: list[dict] | None) -> list[NormalizedAttachment]:
    if not raw:
        return []
    if len(raw) > MAX_ATTACHMENTS:
        raise err(
            413,
            "prompt_too_large",
            f"Demasiados adjuntos (máx {MAX_ATTACHMENTS}).",
        )

    total = 0
    out: list[NormalizedAttachment] = []
    for idx, item in enumerate(raw):
        declared = (item.get("mime") or "").strip().lower()
        if declared not in ALLOWED_MIMES:
            raise err(
                422,
                "attachment_invalid_mime",
                f"Adjunto {idx + 1}: tipo MIME no permitido ({declared or 'vacío'}).",
            )
        b64 = (item.get("data_base64") or "").strip()
        if not b64:
            raise err(422, "attachment_invalid", f"Adjunto {idx + 1}: data_base64 vacío.")
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:
            raise err(422, "attachment_invalid", f"Adjunto {idx + 1}: base64 no válido.") from None
        if not data:
            raise err(422, "attachment_invalid", f"Adjunto {idx + 1}: contenido vacío.")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise err(
                413,
                "prompt_too_large",
                f"Adjunto {idx + 1} supera el máximo de {MAX_ATTACHMENT_BYTES} bytes.",
            )
        total += len(data)
        if total > MAX_ATTACHMENTS_TOTAL_BYTES:
            raise err(
                413,
                "prompt_too_large",
                f"Tamaño agregado de adjuntos supera {MAX_ATTACHMENTS_TOTAL_BYTES} bytes.",
            )
        sniffed = sniff_mime(data)
        if sniffed != declared:
            raise err(
                422,
                "attachment_invalid_mime",
                f"Adjunto {idx + 1}: el contenido no coincide con MIME declarado ({declared}).",
            )
        filename = item.get("filename")
        if filename is not None:
            filename = str(filename).strip()[:255] or None
        out.append(
            NormalizedAttachment(
                mime=declared,
                data=data,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                filename=filename,
            )
        )
    return out
