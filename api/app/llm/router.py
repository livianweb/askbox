"""Orquestación LLM: caché → tier → modelos (model_catalog) → claves (keypool) → proveedor.

Cascada: Gemini primero; si falla (cuota, clave inválida, sin clave) pasa a OpenRouter.
Todas las llamadas a proveedores externas viven bajo app/llm/.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.llm import cache, catalog, keypool
from app.llm.errors import LLMError
from app.llm.providers import gemini, openrouter
from app.llm.schema_check import response_matches_schema

log = logging.getLogger(__name__)

PROVIDER_ORDER = ("gemini", "openrouter")
_PROVIDERS = {"gemini": gemini.complete, "openrouter": openrouter.complete}
_CODE_HINT = re.compile(r"```|\bdef \w+\(|\bclass \w+|function\s+\w+\(|=>|#include|SELECT\s.+\sFROM", re.I | re.S)
TIER_LADDER = ("fast", "balanced", "strong")


class AskFailure(LLMError):
    """Fallo final de la cascada; lleva metadatos para registrar en requests."""

    def __init__(self, message: str, *, tier_used: str, attempts: int, kind: str = "retry", code: str = "provider_failed"):
        super().__init__(message, kind=kind)
        self.tier_used = tier_used
        self.attempts = attempts
        self.code = code


def next_tier(tier: str) -> str | None:
    try:
        i = TIER_LADDER.index(tier)
    except ValueError:
        return None
    return TIER_LADDER[i + 1] if i + 1 < len(TIER_LADDER) else None


def infer_tier_from_text(
    prompt: str,
    max_tokens: int,
    default_tier: str = "auto",
    *,
    json_schema: dict | None = None,
) -> str:
    """Heurística barata para tier=auto (longitud, código, max_tokens, json_schema, default_tier)."""
    if default_tier in TIER_LADDER:
        return default_tier
    if json_schema:
        if json_schema.get("type") == "object" and len(json_schema.get("required") or []) > 3:
            return "strong"
        return "balanced"
    if len(prompt) > 6000 and max_tokens > 2048:
        return "strong"
    if len(prompt) > 1500 or max_tokens > 1024 or _CODE_HINT.search(prompt):
        return "balanced"
    return "fast"


def _schema_key(json_schema: dict | None) -> str:
    if not json_schema:
        return ""
    return json.dumps(json_schema, sort_keys=True, separators=(",", ":"))


async def _cascade(
    pool,
    redis,
    *,
    project: dict,
    messages: list[dict],
    effective_tier: str,
    max_tokens: int,
    json_schema: dict | None,
    prompt_rendered: str,
    ttl: int,
    ckey: str,
) -> dict[str, Any]:
    attempts = 0
    errors: list[str] = []
    any_key = False
    fatal = False
    models_tried: list[str] = []

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
                models_tried.append(f"{provider}:{model['slug']}")
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
                        await keypool.mark_quota(pool, handle, message=str(exc))
                        key_dead = True
                        break
                    if exc.kind == "fatal":
                        fatal = True
                        break
                    continue
                text = (out.get("text") or "").strip()
                if not response_matches_schema(text, json_schema):
                    errors.append(f"{provider}: respuesta vacía o no cumple json_schema")
                    continue
                await keypool.mark_success(pool, handle)
                cin, cout = await catalog.cost_for_model(pool, provider, out["model"])
                in_tok = int(out.get("input_tokens") or 0)
                out_tok = int(out.get("output_tokens") or 0)
                cost = catalog.estimate_cost_usd(in_tok, out_tok, cin, cout)
                result = {
                    "text": text,
                    "provider": provider,
                    "model_used": out["model"],
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "estimated_cost_usd": cost,
                    "models_chain": models_tried,
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
                break
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


async def ask(
    pool,
    redis,
    *,
    project: dict,
    messages: list[dict],
    tier: str,
    max_tokens: int,
    json_schema: dict | None = None,
) -> dict[str, Any]:
    prompt_rendered = "\n".join(f"{m['role']}:{m['content']}" for m in messages)
    schema_s = _schema_key(json_schema)
    requested_auto = tier == "auto"
    effective_tier = (
        infer_tier_from_text(
            prompt_rendered,
            max_tokens,
            project["default_tier"],
            json_schema=json_schema,
        )
        if requested_auto
        else tier
    )

    ckey = cache.cache_key(str(project["id"]), prompt_rendered, effective_tier, max_tokens, schema_s)
    ttl = int(project["cache_ttl_seconds"] or 0)
    if ttl > 0:
        hit = await cache.get(redis, ckey)
        if hit and response_matches_schema((hit.get("text") or ""), json_schema):
            return {
                **hit,
                "cache_hit": True,
                "tier_used": effective_tier,
                "attempts": 0,
                "key_id": None,
                "key_source": None,
                "input_tokens": hit.get("input_tokens") or 0,
                "output_tokens": hit.get("output_tokens") or 0,
                "estimated_cost_usd": hit.get("estimated_cost_usd") or 0.0,
                "models_chain": hit.get("models_chain") or [],
            }

    try:
        res = await _cascade(
            pool,
            redis,
            project=project,
            messages=messages,
            effective_tier=effective_tier,
            max_tokens=max_tokens,
            json_schema=json_schema,
            prompt_rendered=prompt_rendered,
            ttl=ttl,
            ckey=ckey,
        )
    except AskFailure:
        if not requested_auto:
            raise
        escalated = next_tier(effective_tier)
        if not escalated:
            raise
        log.info("auto: escalada única %s → %s", effective_tier, escalated)
        ckey2 = cache.cache_key(str(project["id"]), prompt_rendered, escalated, max_tokens, schema_s)
        res = await _cascade(
            pool,
            redis,
            project=project,
            messages=messages,
            effective_tier=escalated,
            max_tokens=max_tokens,
            json_schema=json_schema,
            prompt_rendered=prompt_rendered,
            ttl=ttl,
            ckey=ckey2,
        )
        res["tier_used"] = escalated
        res["tier_escalated_from"] = effective_tier
        return res
    return res
