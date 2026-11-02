"""Cliente Gemini (una llamada = un modelo; la cadena la decide router.py desde model_catalog)."""
from __future__ import annotations

import base64
import re

import httpx

from app.config import settings
from app.llm.attachments import NormalizedAttachment
from app.llm.errors import LLMError, classify

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Cliente de /v1/route: se reutiliza para no pagar TLS en cada decisión.
_fast_client: httpx.AsyncClient | None = None
_VER = re.compile(r"\d+")


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "error")[:300]
    return f"HTTP {resp.status_code}"


def _attachment_parts(atts: list[NormalizedAttachment]) -> list[dict]:
    parts: list[dict] = []
    for att in atts:
        parts.append(
            {
                "inline_data": {
                    "mime_type": att.mime,
                    "data": base64.b64encode(att.data).decode("ascii"),
                }
            }
        )
    return parts


def _thinking_off(model: str) -> dict:
    """Alias latest y 3.x usan thinkingLevel. El resto, thinkingBudget 0."""
    slug = (model or "").lower()
    nums = [int(n) for n in _VER.findall(slug)]
    major = nums[0] if nums else 0
    if not nums or "latest" in slug or major >= 3:
        return {"thinkingLevel": "minimal"}
    return {"thinkingBudget": 0}


def _fast_http(timeout: float) -> httpx.AsyncClient:
    global _fast_client
    if _fast_client is None or _fast_client.is_closed:
        _fast_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=1.5),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
    return _fast_client


def _to_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    system_bits: list[str] = []
    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role") or "user"
        text = (msg.get("content") or "").strip()
        atts: list[NormalizedAttachment] = msg.get("attachments") or []
        if role == "system":
            if text:
                system_bits.append(text)
            continue
        grole = "user" if role == "user" else "model"
        parts: list[dict] = []
        if text:
            parts.append({"text": text})
        if atts:
            parts.extend(_attachment_parts(atts))
        if not parts:
            continue
        if contents and contents[-1]["role"] == grole and not atts:
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        elif contents and contents[-1]["role"] == grole and atts:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": grole, "parts": parts})
    if not contents:
        raise LLMError("No hay mensajes para enviar a Gemini", kind="fatal")
    return "\n\n".join(system_bits), contents


async def _post(client: httpx.AsyncClient, url: str, headers: dict, payload: dict, model: str) -> httpx.Response:
    try:
        return await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar Gemini ({type(exc).__name__})", kind="retry") from None


async def complete(
    messages: list[dict],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float = 0.4,
    grounding: bool = False,
    fast: bool = False,
    timeout: float | None = None,
) -> dict:
    system, contents = _to_contents(messages)
    payload: dict = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if fast:
        # Sin thinking: si no, el presupuesto de salida se va en razonar y /v1/route no vuelve a tiempo.
        payload["generationConfig"]["thinkingConfig"] = _thinking_off(model)
    if grounding:
        # Google Search grounding (etapa web de mode=allin). Sin esto el payload no cambia.
        payload["tools"] = [{"google_search": {}}]
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    url = GEMINI_API.format(model=model)
    if fast:
        resp = await _post(_fast_http(timeout or 2.5), url, headers, payload, model)
        # Gemini 3 / alias latest rechaza thinkingBudget con 400 "invalid argument", sin decir "thinking".
        if resp.status_code == 400 and "thinkingConfig" in payload["generationConfig"]:
            payload["generationConfig"].pop("thinkingConfig", None)
            resp = await _post(_fast_http(timeout or 2.5), url, headers, payload, model)
    else:
        async with httpx.AsyncClient(timeout=settings.provider_timeout) as client:
            resp = await _post(client, url, headers, payload, model)
    if resp.status_code >= 400:
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", status=resp.status_code, kind=classify(resp.status_code, msg))
    try:
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason") or "sin candidatos"
            raise LLMError(f"{model}: Gemini sin texto ({block})", kind="retry")
        parts = ((cands[0].get("content") or {}).get("parts")) or []
        text = "\n".join(
            p.get("text") or "" for p in parts if isinstance(p, dict) and not p.get("thought")
        ).strip()
    except LLMError:
        raise
    except Exception:
        raise LLMError(f"{model}: respuesta de Gemini no válida", kind="retry") from None
    if not text:
        raise LLMError(f"{model}: respuesta vacía", kind="retry")
    usage = data.get("usageMetadata") if isinstance(data, dict) else {}
    in_tok = int((usage or {}).get("promptTokenCount") or 0)
    out_tok = int((usage or {}).get("candidatesTokenCount") or 0)
    return {"text": text, "model": model, "input_tokens": in_tok, "output_tokens": out_tok}
