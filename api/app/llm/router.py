"""Orquestación LLM: caché → tier → peers (model_catalog) → keypool → proveedor.

Política provider-agnostic: para el tier pedido, peers de misma clase de potencia (S|M|L)
entre todos los proveedores con clave; solo bajar de clase/tier cuando se agotan.
Si el cliente pide provider=ollama (o model=ollama/<slug>), la ruta es local
(`ollama_local`) y un fallo de Ollama no consulta esta cascada. Si el enrutado
automático (modelo omitido o ``auto``) no tiene ningún modelo local activo en
el proyecto, la petición sigue por la cascada de nube.
`mode=allin` o `provider=allin` delega en `allin.py` (híbrido, con fallback).
Sin esos parámetros el camino de cascada no cambia.
Todas las llamadas externas viven bajo app/llm/.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.llm import attachments, cache, catalog, keypool, ollama_local
from app.llm.attachments import NormalizedAttachment
from app.llm.errors import LLMError
from app.llm.heuristics import prompt_text
from app.llm.local_models import resolve_local_model
from app.llm.ollama_route import parse_explicit_provider
from app.llm.providers import gemini, openrouter
from app.llm.schema_check import response_matches_schema

log = logging.getLogger(__name__)

# Orden de desempate entre proveedores (no implica «fallar primero en X»).
PROVIDER_ORDER = ("gemini", "openrouter")
_PROVIDERS = {
    "gemini": gemini.complete,
    "openrouter": openrouter.complete,
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
    require_vision: bool = False,
    temperature: float = 0.4,
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
            "Añade una clave Gemini u OpenRouter al proyecto.",
            tier_used=effective_tier,
            attempts=0,
            kind="fatal",
            code="no_provider_key",
        )

    active = tuple(p for p in PROVIDER_ORDER if p in provider_keys)
    peers = await catalog.peer_chain(
        pool, effective_tier, active, settings.max_models_per_provider, require_vision=require_vision
    )
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
                    temperature=temperature,
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
    attachment_digest: str = "",
    require_vision: bool = False,
    temperature: float = 0.4,
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
                require_vision=require_vision,
                temperature=temperature,
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
                str(project["id"]),
                prompt_rendered,
                tier_attempt,
                max_tokens,
                _schema_key(json_schema),
                attachment_digest,
            )


async def _ask_ollama(
    redis,
    *,
    messages: list[dict],
    tier: str,
    max_tokens: int,
    json_schema: dict | None,
    steps: list[str],
    ttl: int,
    ckey: str,
    temperature: float = 0.4,
) -> dict[str, Any]:
    """Ruta local. Sin keypool, sin peers y sin Gemini/OpenRouter."""
    try:
        out = await ollama_local.execute(
            messages,
            steps=steps,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )
    except ollama_local.LocalFailure as exc:
        raise AskFailure(
            str(exc),
            tier_used=tier,
            attempts=exc.attempts,
            kind="fatal" if exc.kind == "fatal" else "retry",
            code="provider_failed",
        ) from None
    result = {
        "text": out["text"],
        "provider": "ollama",
        "model_used": out["model_used"],
        "input_tokens": out["input_tokens"],
        "output_tokens": out["output_tokens"],
        "estimated_cost_usd": 0.0,
        "models_chain": out["models_chain"],
    }
    await cache.put(redis, ckey, result, ttl)
    return {
        **result,
        "cache_hit": False,
        "tier_used": tier,
        "attempts": out["attempts"],
        "key_id": None,
        "key_source": "local",
    }


async def ask(
    pool,
    redis,
    *,
    project: dict,
    messages: list[dict],
    tier: str,
    max_tokens: int,
    json_schema: dict | None = None,
    normalized_attachments: list[NormalizedAttachment] | None = None,
    task_used: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    if (mode or "").strip().lower() == "allin" or (provider or "").strip().lower() == "allin":
        from app.llm.allin import run_allin

        return await run_allin(
            pool,
            redis,
            project=project,
            messages=messages,
            tier=tier,
            max_tokens=max_tokens,
            json_schema=json_schema,
            normalized_attachments=normalized_attachments,
            task_used=task_used,
        )
    atts = normalized_attachments or []
    explicit: tuple[str, str] | None = None
    if (provider or "").strip() or (model or "").strip():
        try:
            explicit = parse_explicit_provider(provider, model)
        except ValueError as exc:
            raise AskFailure(str(exc), tier_used=tier, attempts=0, kind="fatal", code="provider_failed") from None
    if explicit and atts:
        raise AskFailure(
            "Ollama no admite adjuntos en /v1/ask. Quita attachments o omite provider.",
            tier_used=tier,
            attempts=0,
            kind="fatal",
            code="provider_failed",
        )
    if atts:
        messages = attachments.attach_to_messages(messages, atts)
    prompt_rendered = "\n".join(f"{m['role']}:{m.get('content') or ''}" for m in messages)
    schema_s = _schema_key(json_schema)
    attachment_digest = attachments.attachments_cache_digest(atts)
    temperature = (
        attachments.temperature_for_task(task_used or attachments.TASK_INTERPRET) if atts else 0.4
    )
    require_vision = bool(atts)
    requested_auto = tier == "auto"
    steps: list[str] | None
    if explicit and explicit[1] == "auto":
        # Cuatro rutas locales. Un slug inactivo cae al principal activo.
        # Si no queda ninguno, se deja la cascada (mismo cierre que allin).
        resolved: list[str] = []
        for slug in ollama_local.planned_models(prompt_text(messages)):
            chosen = resolve_local_model(slug, project)
            if not chosen:
                log.info("ollama auto: sin modelo local activo (pedido %s); cascada", slug)
                explicit = None
                resolved = []
                break
            if chosen != slug:
                log.info("ollama auto: %s inactivo; se usa %s", slug, chosen)
            resolved.append(chosen)
        steps = resolved or None
    elif explicit:
        # Modelo concreto: el cliente lo pidió, aunque el proyecto lo tenga inactivo.
        steps = [explicit[1]]
    else:
        steps = None
    # Con Ollama explícito no se infiere ni se escala el tier: no elige modelo.
    # Si el auto local no tenía ningún modelo activo, `explicit` ya es None.
    effective_tier = tier if explicit else (
        infer_tier_from_text(
            prompt_rendered,
            max_tokens,
            project["default_tier"],
            json_schema=json_schema,
        )
        if requested_auto
        else tier
    )
    route = "ollama:" + ">".join(steps) if steps else ""

    ckey = cache.cache_key(
        str(project["id"]),
        prompt_rendered,
        effective_tier,
        max_tokens,
        schema_s,
        attachment_digest,
        route,
    )
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
                "key_source": "local" if explicit else None,
                "input_tokens": hit.get("input_tokens") or 0,
                "output_tokens": hit.get("output_tokens") or 0,
                "estimated_cost_usd": hit.get("estimated_cost_usd") or 0.0,
                "models_chain": hit.get("models_chain") or [],
            }

    if explicit:
        return await _ask_ollama(
            redis,
            messages=messages,
            tier=effective_tier,
            max_tokens=max_tokens,
            json_schema=json_schema,
            steps=steps or [],
            ttl=ttl,
            ckey=ckey,
            temperature=temperature,
        )

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
            attachment_digest=attachment_digest,
            require_vision=require_vision,
            temperature=temperature,
        )
    except AskFailure:
        if not requested_auto:
            raise
        escalated = next_tier(effective_tier)
        if not escalated:
            raise
        log.info("auto: escalada única %s → %s", effective_tier, escalated)
        ckey2 = cache.cache_key(
            str(project["id"]), prompt_rendered, escalated, max_tokens, schema_s, attachment_digest
        )
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
            attachment_digest=attachment_digest,
            require_vision=require_vision,
            temperature=temperature,
        )
        res["tier_used"] = escalated
        res["tier_escalated_from"] = effective_tier
        return res
    return res
