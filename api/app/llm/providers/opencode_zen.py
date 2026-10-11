"""Cliente OpenCode Zen (OpenAI-compatible chat completions)."""
from __future__ import annotations

import httpx

from app.config import settings
from app.llm.errors import LLMError, classify


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    if not isinstance(data, dict):
        return f"HTTP {resp.status_code}"
    if data.get("message") and data.get("code"):
        return str(data.get("message") or "error")[:300]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "error")[:300]
    if isinstance(err, str):
        return err[:300]
    return f"HTTP {resp.status_code}"


async def complete(
    messages: list[dict],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float = 0.4,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if (api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    url = settings.opencode_zen_api_url
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar OpenCode Zen ({type(exc).__name__})", kind="retry") from None
    if resp.status_code >= 400:
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", status=resp.status_code, kind=classify(resp.status_code, msg))
    try:
        data = resp.json()
    except Exception:
        raise LLMError(f"{model}: respuesta de OpenCode Zen no válida", kind="retry") from None
    if isinstance(data, dict) and data.get("error"):
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", kind=classify(None, msg))
    if isinstance(data, dict) and data.get("code") and data.get("message"):
        msg = str(data.get("message"))
        raise LLMError(f"{model}: {msg}", kind=classify(None, msg))
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
