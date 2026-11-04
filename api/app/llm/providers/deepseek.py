"""Cliente DeepSeek nativo (chat completions compatible con OpenAI).

Una llamada = un modelo del catálogo o el id que pidió el cliente
(`deepseek-chat`, `deepseek-reasoner`). No es OpenRouter ni el tag local
`deepseek-r1:14b` de Ollama. Base por defecto: https://api.deepseek.com
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.llm.errors import LLMError, classify

_NO_ATTACHMENTS = "DeepSeek en askbox no admite adjuntos. Quita attachments o usa Gemini."


def chat_completions_url(base_url: str | None = None) -> str:
    base = (settings.deepseek_base_url if base_url is None else base_url).strip().rstrip("/")
    if not base:
        raise LLMError("DEEPSEEK_BASE_URL vacío", kind="fatal")
    lower = base.lower()
    if lower.endswith("/chat/completions"):
        return base
    if lower.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/chat/completions"


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


def _message_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                bits.append(str(part.get("text") or ""))
        return "".join(bits).strip()
    return ""


def _to_deepseek_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.get("attachments"):
            raise LLMError(_NO_ATTACHMENTS, kind="fatal")
        role = msg.get("role") or "user"
        raw = msg.get("content")
        text = raw.strip() if isinstance(raw, str) else _message_text(raw)
        if not text and role != "system":
            continue
        out.append({"role": role, "content": text})
    if not out:
        raise LLMError("No hay mensajes para enviar a DeepSeek", kind="fatal")
    return out


def _choice_text(message: dict) -> str:
    """La respuesta usable va en content. reasoning_content es el trazo interno."""
    return _message_text(message.get("content"))


async def complete(
    messages: list[dict],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float = 0.4,
) -> dict:
    """POST {DEEPSEEK_BASE_URL}/chat/completions. Bearer, sin loguear la clave."""
    payload = {
        "model": model,
        "messages": _to_deepseek_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = chat_completions_url()
    try:
        async with httpx.AsyncClient(timeout=settings.deepseek_timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar DeepSeek ({type(exc).__name__})", kind="retry") from None
    if resp.status_code >= 400:
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", status=resp.status_code, kind=classify(resp.status_code, msg))
    try:
        data = resp.json()
    except Exception:
        raise LLMError(f"{model}: respuesta de DeepSeek no válida", kind="retry") from None
    if isinstance(data, dict) and data.get("error"):
        msg = _error_message(resp)
        kind = classify(None, msg)
        raise LLMError(f"{model}: {msg}", kind=kind if kind != "fatal" else "retry")
    try:
        message = data["choices"][0]["message"]
        text = _choice_text(message if isinstance(message, dict) else {})
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"{model}: respuesta no válida", kind="retry") from None
    if not text:
        raise LLMError(f"{model}: respuesta vacía", kind="retry")
    usage = data.get("usage") if isinstance(data, dict) else {}
    in_tok = int((usage or {}).get("prompt_tokens") or 0)
    out_tok = int((usage or {}).get("completion_tokens") or 0)
    return {"text": text, "model": model, "input_tokens": in_tok, "output_tokens": out_tok}
