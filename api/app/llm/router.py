"""Orquestación LLM: caché → tier → peers (model_catalog) → keypool → proveedor.

Política provider-agnostic: para el tier pedido, peers de misma clase de potencia (S|M|L)
entre todos los proveedores con clave; solo bajar de clase/tier cuando se agotan.
Todas las llamadas externas viven bajo app/llm/.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.llm import cache, catalog, keypool
from app.llm.errors import LLMError
from app.llm.providers import gemini, openrouter, opencode_zen
from app.llm.schema_check import response_matches_schema

log = logging.getLogger(__name__)

# Orden de desempate entre proveedores (no implica «fallar primero en X»).
PROVIDER_ORDER = ("gemini", "openrouter", "opencode_zen")
_PROVIDERS = {
    "gemini": gemini.complete,
    "openrouter": openrouter.complete,
    "opencode_zen": opencode_zen.complete,
}
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


def prev_tier(tier: str) -> str | None:
    try:
        i = TIER_LADDER.index(tier)
    except ValueError:
        return None
    return TIER_LADDER[i - 1] if i > 0 else None


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


def _handle_key_id(handle: keypool.KeyHandle) -> str | None:
    return handle.key_id


async def _cascade_peers(
    pool,
    redis,
    *,
    project: dict,
    messages: list[dict],
    effective_tier: str,
    max_tokens: int,
    json_schema: dict | None,
    ttl: int,
    ckey: str,
) -> dict[str, Any]:
    attempts = 0
    errors: list[str] = []
    fatal = False
    models_tried: list[str] = []

    provider_keys: dict[str, list[keypool.KeyHandle]] = {}
    for provider in PROVIDER_ORDER:
        keys = await keypool.candidates(pool, project, provider)
        if keys:
            provider_keys[provider] = keys

    if not provider_keys:
        raise AskFailure(
            "No hay clave de proveedor utilizable (proyecto o sistema). "
            "Añade una clave Gemini, OpenRouter u OpenCode Zen al proyecto.",
            tier_used=effective_tier,
            attempts=0,
            kind="fatal",
            code="no_provider_key",
        )

    active = tuple(p for p in PROVIDER_ORDER if p in provider_keys)
    peers = await catalog.peer_chain(pool, effective_tier, active, settings.max_models_per_provider)
    if not peers:
        raise AskFailure(
            "Sin modelos habilitados en model_catalog para este tier.",
            tier_used=effective_tier,
            attempts=0,
            kind="fatal",
            code="provider_failed",
        )

    dead_keys: dict[str, set[str | None]] = {p: set() for p in provider_keys}

    for provider, model in peers:
        call = _PROVIDERS[provider]
        for handle in provider_keys[provider]:
            kid = _handle_key_id(handle)
            if kid in dead_keys[provider]:
                continue
            attempts += 1
            tag = f"{provider}:{model['slug']}"
            models_tried.append(tag)
            try:
                out = await call(
                    messages,
                    api_key=handle.secret,
                    model=model["slug"],
                    max_tokens=min(max_tokens, model["max_output_tokens"]),
                )
            except LLMError as exc:
                errors.append(f"{tag}: {exc}")
                log.warning("%s falló (%s): %s", tag, exc.kind, str(exc)[:160])
                if exc.kind == "auth":
                    await keypool.mark_invalid(pool, handle)
                    dead_keys[provider].add(kid)
                    continue
                if exc.kind == "quota":
                    await keypool.mark_quota(pool, handle, message=str(exc))
                    dead_keys[provider].add(kid)
                    continue
                if exc.kind == "fatal":
                    fatal = True
                    continue
                continue
            text = (out.get("text") or "").strip()
            if not response_matches_schema(text, json_schema):
                errors.append(f"{tag}: respuesta vacía o no cumple json_schema")
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

    summary = "; ".join(errors[-4:])[:600]
    raise AskFailure(
        f"Ningún peer respondió en tier {effective_tier}. {summary}",
        tier_used=effective_tier,
        attempts=attempts,
        kind="fatal" if fatal else "retry",
        code="provider_failed",
    )


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
    tier_attempt = effective_tier
    total_attempts = 0
    while True:
        try:
            res = await _cascade_peers(
                pool,
                redis,
                project=project,
                messages=messages,
                effective_tier=tier_attempt,
                max_tokens=max_tokens,
                json_schema=json_schema,
                ttl=ttl,
                ckey=ckey,
            )
            if tier_attempt != effective_tier:
                res["tier_downgraded_from"] = effective_tier
            return res
        except AskFailure as exc:
            total_attempts += exc.attempts
            if exc.code == "no_provider_key":
                raise
            lower = prev_tier(tier_attempt)
            if not lower:
                exc.attempts = total_attempts
                raise
            log.info("cascada: bajada de tier %s → %s (peers agotados)", tier_attempt, lower)
            tier_attempt = lower
            ckey = cache.cache_key(
                str(project["id"]), prompt_rendered, tier_attempt, max_tokens, _schema_key(json_schema)
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
