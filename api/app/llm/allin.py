"""Orquestador híbrido ``mode=allin`` (alias ``provider=allin``).

Etapas que se saltan si no hacen falta:
1. Extracción de adjuntos (OCR / texto de PDF) — solo si hay adjuntos.
2. Resumen web (Gemini con Google Search, si no OpenRouter con plugin web).
3. Contexto local: DeepSeek si el razonamiento es duro; Mistral Nemo si la
   lectura es masiva (prompt u OCR/web ~>8k tokens).
4. Código local (Qwen) si hace falta código.
5. Informe local (Llama) si las etapas anteriores dejaron trabajo intermedio
   sustantivo. El atajo de una sola llamada no informa.
   Si el slug de la etapa está inactivo en el proyecto, se usa el local
   principal activo. Si tras ese remapeo las etapas locales repiten slug,
   no se encadenan: una completion por slug (una sola si todas caen en el
   mismo, con el OCR o el resumen web en el contexto). Si la web falla o
   se omite, tampoco se relanzan varios parciales del mismo modelo.
   Si no hay ninguno activo, esa etapa no llama a Ollama.
   Atajo: sin adjuntos, sin web y complejidad baja, una sola llamada
   (Qwen si es código, Llama si es texto, Mistral si el prompt ya es largo,
   DeepSeek en el resto).

Si una llamada local falla (timeout, conexión, schema), esa etapa —o el cierre—
cae a la cascada Gemini/OpenRouter. Si falla solo el informe, se conserva el
resultado previo. La etapa web, si falla, no tumba la petición.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.llm import attachments, cache, catalog, keypool
from app.llm.attachments import NormalizedAttachment
from app.llm.errors import LLMError
from app.llm.heuristics import (
    allin_needs_report,
    allin_stages,
    estimate_tokens,
    needs_web,
    prompt_text,
    stage_messages,
    visible_answer,
)
from app.llm.local_models import policy_fingerprint, resolve_local_model
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


def collapse_resolved_stages(stages: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """Una entrada ``(purpose, slug)`` por slug, en orden de primera aparición.

    Los slug vacíos se descartan. Un slug que solo planifica o lee, y al que
    sigue otro modelo, conserva ese propósito. Si también escribe código y
    aún queda un modelo distinto, la llamada única es ``code``. Un slug que
    solo informa sigue en ``report``. El resto (responde, o es el último)
    es ``answer``: no se repite el mismo modelo para plan, código e informe.
    """
    order: list[str] = []
    purposes: dict[str, list[str]] = {}
    for purpose, slug in stages:
        text = (slug or "").strip()
        if not text:
            continue
        if text not in purposes:
            order.append(text)
            purposes[text] = []
        if purpose not in purposes[text]:
            purposes[text].append(purpose)
    collapsed: list[tuple[str, str]] = []
    for index, slug in enumerate(order):
        owned = purposes[slug]
        has_later = index < len(order) - 1
        only_context = bool(owned) and set(owned) <= {"plan", "read"}
        if has_later and only_context:
            purpose = "read" if "read" in owned else "plan"
        elif set(owned) == {"report"}:
            purpose = "report"
        elif "code" in owned and has_later:
            purpose = "code"
        else:
            purpose = "answer"
        collapsed.append((purpose, slug))
    return collapsed


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
        "allin:" + policy_fingerprint(project),
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
    context_tokens = estimate_tokens(user_prompt) + estimate_tokens(extracted) + estimate_tokens(web)
    stages = allin_stages(
        user_prompt,
        has_attachments=bool(atts),
        web_needed=needs_web(user_prompt),
        doc_and_web=doc_and_web,
        context_tokens=context_tokens,
    )
    base_ctx = _context_appendix(
        f"Texto extraído de los adjuntos:\n{_clip(extracted, _DOC_LIMIT)}" if extracted else "",
        f"Resumen técnico (web):\n{_clip(web, _WEB_LIMIT)}" if web else "",
    )
    local_models = {
        "code": settings.allin_code_model,
        "reason": settings.allin_reason_model,
        "nlp": settings.allin_nlp_model,
        "longctx": settings.allin_longctx_model,
    }

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

    attempted_local: set[str] = set()
    answered_local: dict[str, dict] = {}

    async def local_stage(model: str, *, purpose: str, appendix: str, final: bool) -> dict | None:
        nonlocal attempts, in_tok, out_tok
        chosen = resolve_local_model(model, project)
        if not chosen:
            log.info("allin: sin modelo local activo (pedido %s); se omite Ollama", model)
            return None
        if chosen in attempted_local:
            log.info("allin: %s ya se usó en esta petición; no se repite", chosen)
            return answered_local.get(chosen)
        if chosen != model:
            log.info("allin: %s inactivo; se usa %s", model, chosen)
        attempted_local.add(chosen)
        payload = stage_messages(messages, purpose=purpose, appendix=appendix)
        attempts += 1
        chain.append(f"ollama:{chosen}")
        try:
            out = await ollama.complete(
                payload,
                model=chosen,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except LLMError as exc:
            log.warning("allin: %s falló (%s); fallback de cascada", chosen, exc.kind)
            return None
        raw = (out.get("text") or "").strip()
        text = visible_answer(raw)
        in_tok += int(out.get("input_tokens") or 0)
        out_tok += int(out.get("output_tokens") or 0)
        if final and not response_matches_schema(text, json_schema):
            log.warning("allin: %s no cumple el contrato de salida; fallback de cascada", chosen)
            return None
        used = out.get("model") if isinstance(out.get("model"), str) else chosen
        result = {"text": text, "full": raw or text, "model_used": (used or chosen).strip(), "provider": "allin"}
        answered_local[chosen] = result
        return result

    async def finish_local_or_cloud(*, model: str, purpose: str, appendix: str) -> dict:
        local = await local_stage(model, purpose=purpose, appendix=appendix, final=True)
        if local is not None:
            return local
        cloud = await cloud_stage(purpose=purpose, appendix=appendix, final=True)
        return _absorb_cloud(cloud or {})

    naive: list[tuple[str, str]] = []
    if stages["shape"] == "simple":
        naive = [("answer", stages["single"])]
    else:
        if stages["context"]:
            naive.append(("read" if stages["context"] == "longctx" else "plan", stages["context"]))
        if stages["code"]:
            naive.append(("code", "code"))
        if not naive:
            naive.append(("answer", "reason"))
        # El informe se cuenta aquí para detectar el mismo slug antes de llamarlo.
        # La ejecución clásica sigue decidiendo el informe con el plan ya escrito.
        if json_schema is None and allin_needs_report(
            shape=stages["shape"],
            has_extracted=bool(extracted.strip()),
            has_web=bool(web.strip()),
            has_plan=bool(stages["code"] and stages["context"]),
            has_code_stage=bool(stages["code"] and stages["context"]),
        ):
            naive.append(("report", "nlp"))
    resolved = [(purpose, resolve_local_model(local_models[role], project)) for purpose, role in naive]
    slugs = [slug for _, slug in resolved if slug]
    repeated = len(slugs) != len(set(slugs))

    async def _run_collapsed(pairs: list[tuple[str, str | None]]) -> dict:
        collapsed = collapse_resolved_stages(pairs)
        if not collapsed:
            cloud_only = await cloud_stage(purpose="answer", appendix=base_ctx, final=True)
            return _absorb_cloud(cloud_only or {})
        plan_text = ""
        title = "Plan de diseño"
        closed_local: dict | None = None
        for idx, (purpose, slug) in enumerate(collapsed):
            last = idx == len(collapsed) - 1
            if purpose == "read":
                title = "Lectura del contexto"
            parts = [base_ctx]
            if purpose in ("code", "answer", "report") and plan_text:
                parts.append(f"{title}:\n{_clip(plan_text, _PLAN_LIMIT)}")
            if purpose == "report" and closed_local and closed_local.get("text"):
                parts.append(f"Resultado de la tarea:\n{_clip(closed_local['text'], _DOC_LIMIT)}")
            appendix = _context_appendix(*parts)
            final = purpose not in ("plan", "read")
            local = await local_stage(slug, purpose=purpose, appendix=appendix, final=final)
            if local is None:
                if purpose in ("plan", "read") and not last:
                    cloud_plan = await cloud_stage(purpose=purpose, appendix=appendix, final=False)
                    if cloud_plan:
                        absorbed = _absorb_cloud(cloud_plan)
                        plan_text = absorbed["text"]
                    continue
                if purpose == "report" and closed_local is not None:
                    try:
                        cloud_report = await cloud_stage(purpose=purpose, appendix=appendix, final=True)
                    except AskFailure:
                        log.warning("allin: el informe no pudo generarse; se conserva el resultado previo")
                    else:
                        if cloud_report:
                            closed_local = _absorb_cloud(cloud_report)
                    continue
                cloud_final = await cloud_stage(purpose=purpose, appendix=appendix, final=True)
                absorbed = _absorb_cloud(cloud_final or {})
                if purpose in ("plan", "read"):
                    plan_text = absorbed["text"]
                closed_local = absorbed
                continue
            if purpose in ("plan", "read"):
                plan_text = local["full"]
                if last:
                    closed_local = local
            else:
                closed_local = local
        if closed_local is None:
            raise AskFailure(
                "allin no produjo respuesta",
                tier_used=effective_tier,
                attempts=attempts,
                code="provider_failed",
            )
        return closed_local

    plan = ""
    note_title = "Plan de diseño"
    if repeated:
        log.info("allin: etapas locales repetían %s; una llamada por slug", ">".join(slugs))
        closed = await _run_collapsed(resolved)
    elif stages["shape"] == "simple":
        closed = await finish_local_or_cloud(
            model=local_models[stages["single"]],
            purpose="answer",
            appendix=base_ctx,
        )
    else:
        closed = None
        if stages["context"]:
            ctx_purpose = "read" if stages["context"] == "longctx" else "plan"
            note_title = "Lectura del contexto" if ctx_purpose == "read" else "Plan de diseño"
            ctx_model = local_models[stages["context"]]
            ctx_final = not stages["code"]
            reasoned = await local_stage(ctx_model, purpose=ctx_purpose, appendix=base_ctx, final=ctx_final)
            if reasoned is None:
                cloud_plan = await cloud_stage(purpose=ctx_purpose, appendix=base_ctx, final=ctx_final)
                if cloud_plan:
                    absorbed = _absorb_cloud(cloud_plan)
                    plan = absorbed["text"]
                    if ctx_final:
                        closed = absorbed
            else:
                plan = reasoned["full"]
                if ctx_final:
                    closed = reasoned
        if stages["code"]:
            code_appendix = _context_appendix(
                base_ctx,
                f"{note_title}:\n{_clip(plan, _PLAN_LIMIT)}" if plan else "",
            )
            closed = await finish_local_or_cloud(
                model=local_models["code"],
                purpose="code",
                appendix=code_appendix,
            )
        if closed is None:
            closed = await finish_local_or_cloud(
                model=local_models["reason"],
                purpose="answer",
                appendix=base_ctx,
            )

    # El plan de un razonamiento suelto es la propia respuesta: no lo dupliques en el informe.
    # Si ya colapsamos slugs repetidos, el informe iba dentro de esa única llamada.
    plan_for_report = plan if stages["code"] else ""
    if not repeated and json_schema is None and allin_needs_report(
        shape=stages["shape"],
        has_extracted=bool(extracted.strip()),
        has_web=bool(web.strip()),
        has_plan=bool(plan_for_report.strip()),
        has_code_stage=bool(stages["code"] and stages["context"]),
    ):
        prior = closed
        report_appendix = _context_appendix(
            base_ctx,
            f"{note_title}:\n{_clip(plan_for_report, _PLAN_LIMIT)}" if plan_for_report else "",
            f"Resultado de la tarea:\n{_clip(prior.get('text') or '', _DOC_LIMIT)}" if prior.get("text") else "",
        )
        reported = await local_stage(
            local_models["nlp"],
            purpose="report",
            appendix=report_appendix,
            final=True,
        )
        if reported is not None:
            closed = reported
        else:
            try:
                cloud_report = await cloud_stage(purpose="report", appendix=report_appendix, final=True)
            except AskFailure:
                log.warning("allin: el informe no pudo generarse; se conserva el resultado previo")
            else:
                if cloud_report:
                    closed = _absorb_cloud(cloud_report)

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
