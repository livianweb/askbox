"""Cliente Gemini (una llamada = un modelo; la cadena la decide router.py desde model_catalog)."""
from __future__ import annotations

import httpx

from app.config import settings
from app.llm.errors import LLMError, classify

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "error")[:300]
    return f"HTTP {resp.status_code}"


def _to_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    system_bits: list[str] = []
    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role") or "user"
        text = (msg.get("content") or "").strip()
        if not text:
            continue
        if role == "system":
            system_bits.append(text)
            continue
        grole = "user" if role == "user" else "model"
        if contents and contents[-1]["role"] == grole:
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": grole, "parts": [{"text": text}]})
    if not contents:
        raise LLMError("No hay mensajes para enviar a Gemini", kind="fatal")
    return "\n\n".join(system_bits), contents


async def complete(messages: list[dict], *, api_key: str, model: str, max_tokens: int, temperature: float = 0.4) -> dict:
    system, contents = _to_contents(messages)
    payload: dict = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout) as client:
            resp = await client.post(GEMINI_API.format(model=model), headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar Gemini ({type(exc).__name__})", kind="retry") from None
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
        text = "\n".join(p.get("text") or "" for p in parts if isinstance(p, dict)).strip()
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
