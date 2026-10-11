"""Orquestación LLM: caché → tier → modelos (model_catalog) → claves (keypool) → proveedor.

Cascada: Gemini primero; si falla (cuota, clave inválida, sin clave) pasa a OpenRouter.
Todas las llamadas a proveedores externas viven bajo app/llm/.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.config import settings
from app.llm import cache, catalog, keypool
from app.llm.errors import LLMError
from app.llm.providers import gemini, openrouter

log = logging.getLogger(__name__)

PROVIDER_ORDER = ("gemini", "openrouter")
_PROVIDERS = {"gemini": gemini.complete, "openrouter": openrouter.complete}
_CODE_HINT = re.compile(r"```|\bdef \w+\(|\bclass \w+|function\s+\w+\(|=>|#include|SELECT\s.+\sFROM", re.I | re.S)


class AskFailure(LLMError):
    """Fallo final de la cascada; lleva metadatos para registrar en requests."""

    def __init__(self, message: str, *, tier_used: str, attempts: int, kind: str = "retry", code: str = "provider_failed"):
        super().__init__(message, kind=kind)
        self.tier_used = tier_used
        self.attempts = attempts
        self.code = code


def infer_tier_from_text(prompt: str, max_tokens: int, default_tier: str = "auto") -> str:
    """Heurística barata para tier=auto (longitud, código, max_tokens). default_tier del proyecto manda si no es auto."""
    if default_tier in {"fast", "balanced", "strong"}:
        return default_tier
    if len(prompt) > 6000 and max_tokens > 2048:
        return "strong"
    if len(prompt) > 1500 or max_tokens > 1024 or _CODE_HINT.search(prompt):
        return "balanced"
    return "fast"


async def ask(
    pool,
    redis,
    *,
    project: dict,
    messages: list[dict],
    tier: str,
    max_tokens: int,
) -> dict[str, Any]:
    prompt_rendered = "\n".join(f"{m['role']}:{m['content']}" for m in messages)
    effective_tier = infer_tier_from_text(prompt_rendered, max_tokens, project["default_tier"]) if tier == "auto" else tier

    ckey = cache.cache_key(str(project["id"]), prompt_rendered, effective_tier, max_tokens)
    ttl = int(project["cache_ttl_seconds"] or 0)
    if ttl > 0:
        hit = await cache.get(redis, ckey)
        if hit:
            return {**hit, "cache_hit": True, "tier_used": effective_tier, "attempts": 0, "key_id": None, "key_source": None}

    attempts = 0
    errors: list[str] = []
    any_key = False
    fatal = False

    for provider in PROVIDER_ORDER:
        keys = await keypool.candidates(pool, project, provider)
        if not keys:
            errors.append(f"{provider}: sin clave disponible")
            continue
        any_key = True
        models = await catalog.chain(pool, effective_tier, provider, settings.max_models_per_provider)
        if not models:
            errors.append(f"{provider}: sin modelos habilitados en model_catalog")
            continue
        call = _PROVIDERS[provider]
        for handle in keys:
            key_dead = False
            for model in models:
                attempts += 1
                try:
                    out = await call(
                        messages,
                        api_key=handle.secret,
                        model=model["slug"],
                        max_tokens=min(max_tokens, model["max_output_tokens"]),
                    )
                except LLMError as exc:
                    errors.append(f"{provider}: {exc}")
                    log.warning("%s falló (%s): %s", provider, exc.kind, str(exc)[:160])
                    if exc.kind == "auth":
                        await keypool.mark_invalid(pool, handle)
                        key_dead = True
                        break
                    if exc.kind == "quota":
                        await keypool.mark_quota(pool, handle)
                        key_dead = True
                        break
                    if exc.kind == "fatal":
                        fatal = True
                        break
                    continue  # retry: siguiente modelo
                await keypool.mark_success(pool, handle)
                result = {
                    "text": out["text"],
                    "provider": provider,
                    "model_used": out["model"],
                }
                await cache.put(redis, ckey, result, ttl)
                return {
                    **result,
                    "cache_hit": False,
                    "tier_used": effective_tier,
                    "attempts": attempts,
                    "key_id": handle.key_id,
                    "key_source": handle.source,
                }
            if not key_dead:
                break  # los modelos fallaron por sí mismos: otra clave no ayuda
        # siguiente proveedor

    if not any_key:
        raise AskFailure(
            "No hay clave de proveedor utilizable (proyecto o sistema). Añade una clave Gemini/OpenRouter al proyecto.",
            tier_used=effective_tier,
            attempts=attempts,
            kind="fatal",
            code="no_provider_key",
        )
    summary = "; ".join(errors[-4:])[:600]
    raise AskFailure(
        f"Ningún proveedor respondió. {summary}",
        tier_used=effective_tier,
        attempts=attempts,
        kind="fatal" if fatal else "retry",
        code="provider_failed",
    )
