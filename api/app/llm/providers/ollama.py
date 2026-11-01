"""Cliente Ollama vía API compatible con OpenAI (/v1/chat/completions).

Una llamada = el modelo que pidió el cliente (o OLLAMA_MODEL). No usa clave
de keypool: el servicio es local en la red Compose. No forma parte de la cascada.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.llm.errors import LLMError, classify

_NO_ATTACHMENTS = "Ollama en askbox no admite adjuntos. Quita attachments o omite provider."


def chat_completions_url(base_url: str | None = None) -> str:
    base = (settings.ollama_base_url if base_url is None else base_url).strip().rstrip("/")
    if not base:
        raise LLMError("OLLAMA_BASE_URL vacío", kind="fatal")
    lower = base.lower()
    if lower.endswith("/chat/completions"):
        return base
    if lower.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


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


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.get("attachments"):
            raise LLMError(_NO_ATTACHMENTS, kind="fatal")
        role = msg.get("role") or "user"
        text = (msg.get("content") or "").strip() if isinstance(msg.get("content"), str) else _message_text(msg.get("content"))
        if not text and role != "system":
            continue
        out.append({"role": role, "content": text})
    if not out:
        raise LLMError("No hay mensajes para enviar a Ollama", kind="fatal")
    return out


async def complete(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    temperature: float = 0.4,
) -> dict:
    """POST {OLLAMA_BASE_URL}/v1/chat/completions. Sin Authorization."""
    payload = {
        "model": model,
        "messages": _to_ollama_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = chat_completions_url()
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
            resp = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"{model}: no se pudo contactar Ollama ({type(exc).__name__})", kind="retry") from None
    if resp.status_code >= 400:
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", status=resp.status_code, kind=classify(resp.status_code, msg))
    try:
        data = resp.json()
    except Exception:
        raise LLMError(f"{model}: respuesta de Ollama no válida", kind="retry") from None
    if isinstance(data, dict) and data.get("error"):
        msg = _error_message(resp)
        raise LLMError(f"{model}: {msg}", kind=classify(None, msg) if classify(None, msg) != "fatal" else "retry")
    try:
        text = _message_text(data["choices"][0]["message"].get("content"))
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"{model}: respuesta no válida", kind="retry") from None
    if not text:
        raise LLMError(f"{model}: respuesta vacía", kind="retry")
    usage = data.get("usage") if isinstance(data, dict) else {}
    in_tok = int((usage or {}).get("prompt_tokens") or 0)
    out_tok = int((usage or {}).get("completion_tokens") or 0)
    used = data.get("model") if isinstance(data, dict) else None
    if not isinstance(used, str) or not used.strip():
        used = model
    return {"text": text, "model": used.strip(), "input_tokens": in_tok, "output_tokens": out_tok}
