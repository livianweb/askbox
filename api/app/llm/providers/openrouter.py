"""Cliente OpenRouter (una llamada = un modelo)."""
from __future__ import annotations

import base64

import httpx

from app.config import settings
from app.llm.attachments import NormalizedAttachment
from app.llm.errors import LLMError, classify

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter (chat/completions): imágenes vía image_url; PDF no tiene camino estable aquí — usar Gemini.
_OPENROUTER_PDF_MSG = (
    "PDF no soportado en OpenRouter desde askbox; enruta con proveedor Gemini o quita el PDF."
)


def _ascii(value: str, fallback: str) -> str:
    return value.encode("ascii", "ignore").decode("ascii") or fallback


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "error")[:300]
    if isinstance(err, str):
        return err[:300]
    return f"HTTP {resp.status_code}"


def _to_openrouter_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role") or "user"
        text = (msg.get("content") or "").strip()
        atts: list[NormalizedAttachment] = msg.get("attachments") or []
        if not atts:
            if not text and role != "system":
                continue
            out.append({"role": role, "content": text})
            continue
        for att in atts:
            if att.mime == "application/pdf":
                raise LLMError(_OPENROUTER_PDF_MSG, kind="retry")
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for att in atts:
            if not att.mime.startswith("image/"):
                raise LLMError(f"Tipo {att.mime} no soportado en OpenRouter.", kind="retry")
            b64 = base64.b64encode(att.data).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{att.mime};base64,{b64}"},
                }
            )
        out.append({"role": role, "content": blocks})
    return out


async def complete(messages: list[dict], *, api_key: str, model: str, max_tokens: int, temperature: float = 0.4) -> dict:
    or_messages = _to_openrouter_messages(messages)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": _ascii(settings.openrouter_referer, "https://askbox.local"),
        "X-Title": _ascii(settings.openrouter_title, "askbox"),
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": or_messages, "temperature": temperature, "max_tokens": max_tokens}
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout) as client:
            resp = await client.post(OPENROUTER_API, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar OpenRouter ({type(exc).__name__})", kind="retry") from None
    if resp.status_code >= 400:
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", status=resp.status_code, kind=classify(resp.status_code, msg))
    try:
        data = resp.json()
    except Exception:
        raise LLMError(f"{model}: respuesta de OpenRouter no válida", kind="retry") from None
    if isinstance(data, dict) and data.get("error"):
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", kind=classify(None, msg) if classify(None, msg) != "fatal" else "retry")
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"{model}: respuesta no válida", kind="retry") from None
    if not text:
        raise LLMError(f"{model}: respuesta vacía", kind="retry")
    usage = data.get("usage") if isinstance(data, dict) else {}
    in_tok = int((usage or {}).get("prompt_tokens") or 0)
    out_tok = int((usage or {}).get("completion_tokens") or 0)
    return {"text": text, "model": model, "input_tokens": in_tok, "output_tokens": out_tok}
