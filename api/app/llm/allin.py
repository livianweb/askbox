"""Orquestador híbrido ``mode=allin`` (alias ``provider=allin``).

Etapas que se saltan si no hacen falta:
1. Extracción de adjuntos (OCR / texto de PDF) — solo si hay adjuntos.
2. Resumen web (Gemini con Google Search, si no OpenRouter con plugin web).
3. Razonamiento local (DeepSeek) si la tarea es compleja o vino de PDF+web.
4. Código local (Qwen) si hace falta código.
5. Atajo: sin adjuntos, sin web y complejidad baja, una sola llamada local
   (Qwen si es código, DeepSeek si es lógica).

Si una llamada local falla (timeout, conexión, schema), esa etapa —o el cierre—
cae a la cascada Gemini/OpenRouter. La etapa web, si falla, no tumba la petición.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.llm import attachments, cache, catalog, keypool
from app.llm.attachments import NormalizedAttachment
from app.llm.errors import LLMError
from app.llm.heuristics import (
    allin_shape,
    needs_code,
    needs_web,
    prompt_text,
    stage_messages,
    visible_answer,
)
from app.llm.providers import gemini, ollama, openrouter
from app.llm.schema_check import response_matches_schema
from app.ocr import engine as ocr_engine

log = logging.getLogger(__name__)

_DOC_LIMIT = 16_000
_WEB_LIMIT = 4_000
_PLAN_LIMIT = 8_000


def _clip(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n…"


def _rendered(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role')}:{m.get('content') or ''}" for m in messages)


async def extract_documents(atts: list[NormalizedAttachment]) -> str:
    """Texto plano por adjunto, reutilizando el motor de ``/v1/ocr``.

    Un adjunto que falle no aborta el resto: se registra y se sigue.
    """
    chunks: list[str] = []
    for att in atts:
        try:
            if att.mime == "application/pdf":
                count = await ocr_engine.run_limited(ocr_engine.page_count, att.data, att.mime)
                pages = max(1, min(int(count or 1), max(1, settings.pdf_max_pages)))
                page_numbers = range(1, pages + 1)
            else:
                page_numbers = range(1, 2)
            for page in page_numbers:
                res = await ocr_engine.run_limited(
                    ocr_engine.read_page, att.data, att.mime, page, "spa", 4,
                )
                lines = getattr(res, "lines", None) or []
                text = "\n".join(
                    (ln.get("text") or "").strip() for ln in lines if isinstance(ln, dict)
                ).strip()
                if text:
                    label = att.filename or att.mime
                    chunks.append(f"[{label} p{page}]\n{text}")
        except Exception as exc:  # noqa: BLE001 — un adjunto ilegible no tumba allin
            log.warning("allin: extracción de adjunto falló (%s)", type(exc).__name__)
    return "\n\n".join(chunks).strip()


async def _mark_key(pool, handle, exc: LLMError) -> None:
    try:
        if exc.kind == "auth":
            await keypool.mark_invalid(pool, handle)
        elif exc.kind == "quota":
            await keypool.mark_quota(pool, handle, message=str(exc))
    except Exception as mark_exc:  # noqa: BLE001
        log.warning("allin: no se pudo marcar la clave (%s)", type(mark_exc).__name__)


async def web_summary(pool, project: dict, prompt: str, *, max_tokens: int) -> dict:
    """Un resumen técnico. Primero Gemini con grounding; si no, OpenRouter web.

    Lanza ``LLMError`` si ningún proveedor con clave respondió. El llamador
    decide seguir sin resumen.
    """
    if pool is None:
        raise LLMError("sin base de datos para la etapa web", kind="fatal")
    messages = [
        {
            "role": "system",
            "content": (
                "Resume en un párrafo técnico solo hechos actuales útiles para la pregunta. "
                "Sin código largo ni preámbulos."
            ),
        },
        {"role": "user", "content": _clip(prompt, 6000) or "Resume el dato actual pedido."},
    ]
    cap = min(384, max(32, max_tokens))
    errors: list[str] = []
    for provider in ("gemini", "openrouter"):
        try:
            keys = await keypool.candidates(pool, project, provider)
        except Exception as exc:  # noqa: BLE001
            log.warning("allin: claves %s no disponibles (%s)", provider, type(exc).__name__)
            continue
        if not keys:
            continue
        try:
            peers = await catalog.peer_chain(pool, "fast", (provider,), 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("allin: catálogo %s no disponible (%s)", provider, type(exc).__name__)
            continue
        if not peers:
            continue
        model = peers[0][1]
        call = gemini.complete if provider == "gemini" else openrouter.complete
        extra = {"grounding": True} if provider == "gemini" else {"web_search": True}
        for handle in keys:
            try:
                out = await call(
                    messages,
                    api_key=handle.secret,
                    model=model["slug"],
                    max_tokens=min(cap, int(model["max_output_tokens"] or cap)),
                    temperature=0.2,
                    **extra,
                )
            except LLMError as exc:
                errors.append(f"{provider}:{model['slug']}")
                log.warning("allin: web %s falló (%s)", provider, exc.kind)
                await _mark_key(pool, handle, exc)
                continue
            text = visible_answer(out.get("text") or "")
            if not text:
                continue
            try:
                await keypool.mark_success(pool, handle)
            except Exception as exc:  # noqa: BLE001
                log.warning("allin: mark_success web (%s)", type(exc).__name__)
            try:
                cin, cout = await catalog.cost_for_model(pool, provider, out.get("model") or model["slug"])
            except Exception:  # noqa: BLE001
                cin, cout = 0.0, 0.0
            in_tok = int(out.get("input_tokens") or 0)
            out_tok = int(out.get("output_tokens") or 0)
            used = out.get("model") if isinstance(out.get("model"), str) else model["slug"]
            return {
                "text": text,
                "provider": provider,
                "model_used": used,
                "models_chain": [f"{provider}:{used}"],
                "attempts": 1,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "estimated_cost_usd": catalog.estimate_cost_usd(in_tok, out_tok, cin, cout),
                "key_id": handle.key_id,
                "key_source": handle.source,
            }
    raise LLMError(errors[-1] if errors else "sin clave para la etapa web", kind="retry")


def _context_appendix(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p and p.strip())


async def run_allin(
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
) -> dict:
    from app.llm.router import AskFailure, _cascade, infer_tier_from_text

    atts = list(normalized_attachments or [])
    user_prompt = prompt_text(messages)
    requested_auto = tier == "auto"
    effective_tier = (
        infer_tier_from_text(
            user_prompt,
            max_tokens,
            project.get("default_tier") or "auto",
            json_schema=json_schema,
        )
        if requested_auto
        else tier
    )
    temperature = (
        attachments.temperature_for_task(task_used or attachments.TASK_INTERPRET) if atts else 0.4
    )
    schema_s = ""
    if json_schema:
        import json

        schema_s = json.dumps(json_schema, sort_keys=True, separators=(",", ":"))
    digest = attachments.attachments_cache_digest(atts)
    ckey = cache.cache_key(
        str(project["id"]),
        _rendered(messages),
        effective_tier,
        max_tokens,
        schema_s,
        digest,
        "allin",
    )
    ttl = int(project.get("cache_ttl_seconds") or 0)
    if ttl > 0:
        hit = await cache.get(redis, ckey)
        if hit and response_matches_schema(hit.get("text") or "", json_schema):
            return {
                **hit,
                "cache_hit": True,
                "tier_used": effective_tier,
                "attempts": 0,
                "key_id": hit.get("key_id"),
                "key_source": hit.get("key_source"),
                "input_tokens": hit.get("input_tokens") or 0,
                "output_tokens": hit.get("output_tokens") or 0,
                "estimated_cost_usd": hit.get("estimated_cost_usd") or 0.0,
                "models_chain": hit.get("models_chain") or [],
            }

    chain: list[str] = []
    attempts = 0
    in_tok = 0
    out_tok = 0
    cost = 0.0
    key_id = None
    key_source = "local"

    extracted = ""
    if atts:
        extracted = await extract_documents(atts)
        chain.append("allin:ocr")

    web = ""
    if needs_web(user_prompt):
        try:
            summary = await web_summary(pool, project, user_prompt, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001 — la web no tumba el resto
            log.warning("allin: etapa web omitida (%s)", type(exc).__name__)
            summary = None
        if summary and summary.get("text"):
            web = summary["text"]
            chain.append("allin:web")
            chain.extend(summary.get("models_chain") or [])
            attempts += int(summary.get("attempts") or 0)
            in_tok += int(summary.get("input_tokens") or 0)
            out_tok += int(summary.get("output_tokens") or 0)
            cost += float(summary.get("estimated_cost_usd") or 0)
            key_id = summary.get("key_id")
            key_source = summary.get("key_source") or key_source

    doc_and_web = bool(extracted.strip()) and bool(web.strip())
    shape = allin_shape(
        user_prompt,
        has_attachments=bool(atts),
        web_needed=needs_web(user_prompt),
        doc_and_web=doc_and_web,
    )
    base_ctx = _context_appendix(
        f"Texto extraído de los adjuntos:\n{_clip(extracted, _DOC_LIMIT)}" if extracted else "",
        f"Resumen técnico (web):\n{_clip(web, _WEB_LIMIT)}" if web else "",
    )
    reason_model = settings.allin_reason_model
    code_model = settings.allin_code_model
    simple_model = code_model if needs_code(user_prompt) else reason_model

    async def cloud_stage(*, purpose: str, appendix: str, final: bool) -> dict | None:
        payload = stage_messages(messages, purpose=purpose, appendix=appendix)
        vision = bool(atts) and not extracted.strip()
        if vision:
            payload = attachments.attach_to_messages(payload, atts)
        try:
            res = await _cascade(
                pool,
                redis,
                project=project,
                messages=payload,
                effective_tier=effective_tier,
                max_tokens=max_tokens,
                json_schema=json_schema if final else None,
                prompt_rendered=_rendered(payload),
                ttl=0,
                ckey=ckey,
                attachment_digest=digest if vision else "",
                require_vision=vision,
                temperature=temperature,
            )
        except AskFailure as exc:
            if final:
                raise
            log.warning("allin: fallback de etapa intermedia agotado (%s)", exc.code)
            return None
        return res

    def _absorb_cloud(res: dict) -> dict:
        nonlocal attempts, in_tok, out_tok, cost, key_id, key_source
        chain.extend(res.get("models_chain") or [])
        attempts += int(res.get("attempts") or 0)
        in_tok += int(res.get("input_tokens") or 0)
        out_tok += int(res.get("output_tokens") or 0)
        cost += float(res.get("estimated_cost_usd") or 0)
        key_id = res.get("key_id")
        key_source = res.get("key_source") or key_source
        # La cascada ya validó json_schema sobre este texto; no lo reescribas.
        text = (res.get("text") or "").strip()
        return {
            "text": text,
            "provider": res.get("provider") or "gemini",
            "model_used": res.get("model_used") or "",
        }

    async def local_stage(model: str, *, purpose: str, appendix: str, final: bool) -> dict | None:
        nonlocal attempts, in_tok, out_tok
        payload = stage_messages(messages, purpose=purpose, appendix=appendix)
        attempts += 1
        chain.append(f"ollama:{model}")
        try:
            out = await ollama.complete(
                payload,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except LLMError as exc:
            log.warning("allin: %s falló (%s); fallback de cascada", model, exc.kind)
            return None
        raw = (out.get("text") or "").strip()
        text = visible_answer(raw)
        in_tok += int(out.get("input_tokens") or 0)
        out_tok += int(out.get("output_tokens") or 0)
        if final and not response_matches_schema(text, json_schema):
            log.warning("allin: %s no cumple el contrato de salida; fallback de cascada", model)
            return None
        used = out.get("model") if isinstance(out.get("model"), str) else model
        return {"text": text, "full": raw or text, "model_used": (used or model).strip(), "provider": "allin"}

    async def finish_local_or_cloud(*, model: str, purpose: str, appendix: str) -> dict:
        local = await local_stage(model, purpose=purpose, appendix=appendix, final=True)
        if local is not None:
            return local
        cloud = await cloud_stage(purpose=purpose, appendix=appendix, final=True)
        return _absorb_cloud(cloud or {})

    plan = ""
    if shape == "plan_code":
        plan_appendix = base_ctx
        reasoned = await local_stage(reason_model, purpose="plan", appendix=plan_appendix, final=False)
        if reasoned is None:
            cloud_plan = await cloud_stage(purpose="plan", appendix=plan_appendix, final=False)
            if cloud_plan:
                absorbed = _absorb_cloud(cloud_plan)
                plan = absorbed["text"]
        else:
            plan = reasoned["full"]
        code_appendix = _context_appendix(base_ctx, f"Plan de diseño:\n{_clip(plan, _PLAN_LIMIT)}" if plan else "")
        closed = await finish_local_or_cloud(model=code_model, purpose="code", appendix=code_appendix)
    elif shape == "simple":
        closed = await finish_local_or_cloud(model=simple_model, purpose="answer", appendix=base_ctx)
    elif shape == "code":
        closed = await finish_local_or_cloud(model=code_model, purpose="code", appendix=base_ctx)
    else:
        closed = await finish_local_or_cloud(model=reason_model, purpose="plan", appendix=base_ctx)

    result = {
        "text": closed["text"],
        "provider": closed["provider"],
        "model_used": closed["model_used"],
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_cost_usd": cost,
        "models_chain": chain,
        "key_id": key_id,
        "key_source": key_source,
    }
    await cache.put(redis, ckey, result, ttl)
    return {
        **result,
        "cache_hit": False,
        "tier_used": effective_tier,
        "attempts": attempts,
    }
