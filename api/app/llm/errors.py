"""Errores del módulo LLM (mensajes resumidos, sin cabeceras ni claves)."""
from __future__ import annotations

# kind:
#   quota -> cuota/429: poner la clave en cooldown y probar otra
#   auth  -> clave inválida (401/403 no cuota): marcar invalid
#   retry -> fallo transitorio del modelo: probar siguiente modelo
#   fatal -> petición mal formada u otro error no reintentable
QUOTA_NEEDLES = ("quota", "rate limit", "rate-limit", "resource exhausted", "too many requests", "free-models-per-day")
RETRY_NEEDLES = (
    "quota", "rate limit", "rate-limit", "resource exhausted", "high demand", "unavailable",
    "too many requests", "try again", "not found", "no longer available", "no endpoints",
    "requires payment", "payment required", "data policy", "overloaded",
)
AUTH_NEEDLES = ("api key not valid", "invalid api key", "api_key_invalid", "invalid_api_key", "unauthorized", "no auth credentials")
RETRY_STATUSES = {402, 403, 404, 408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, kind: str = "retry"):
        super().__init__(message)
        self.status = status
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in {"quota", "retry"}


def classify(status: int | None, message: str) -> str:
    lower = (message or "").lower()
    if status == 429 or any(n in lower for n in QUOTA_NEEDLES):
        return "quota"
    if status == 401:
        return "auth"
    if status == 403 and not any(n in lower for n in RETRY_NEEDLES):
        return "auth"
    if any(n in lower for n in AUTH_NEEDLES):
        return "auth"
    if (status in RETRY_STATUSES) or any(n in lower for n in RETRY_NEEDLES):
        return "retry"
    return "fatal"


class NoKeyError(LLMError):
    """No hay clave utilizable (proyecto ni sistema) para ningún proveedor."""

    def __init__(self, message: str):
        super().__init__(message, kind="fatal")
