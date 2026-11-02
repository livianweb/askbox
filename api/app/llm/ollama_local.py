"""Ruta ``provider=ollama``: 100 % local.

No importa clientes de Gemini ni OpenRouter, no busca en la web y no hace
fallback a la cascada. Un fallo de Ollama se propaga como ``LocalFailure``.
"""
from __future__ import annotations

from app.config import settings
from app.llm.errors import LLMError
from app.llm.heuristics import ollama_auto_kind, stage_messages, visible_answer
from app.llm.providers import ollama
from app.llm.schema_check import response_matches_schema


class LocalFailure(Exception):
    def __init__(self, message: str, *, attempts: int, kind: str = "retry"):
        super().__init__(message)
        self.attempts = attempts
        self.kind = kind


def dedupe_steps(steps: list[str]) -> list[str]:
    """Un slug por petición. Conserva el orden de la primera aparición.

    Razón y código que resuelven al mismo modelo no se llaman dos veces.
    """
    out: list[str] = []
    for raw in steps:
        slug = (raw or "").strip()
        if slug and slug not in out:
            out.append(slug)
    return out


def planned_models(prompt: str) -> list[str]:
    """Modelo o par de modelos cuando el cliente no fija uno (o pide ``auto``)."""
    kind = ollama_auto_kind(prompt)
    if kind == "pipeline":
        return [settings.ollama_reason_model, settings.allin_code_model]
    if kind == "reason":
        return [settings.ollama_reason_model]
    if kind == "nlp":
        return [settings.ollama_nlp_model]
    if kind == "longctx":
        return [settings.ollama_longctx_model]
    return [settings.allin_code_model]


async def execute(
    messages: list[dict],
    *,
    steps: list[str],
    max_tokens: int,
    temperature: float,
    json_schema: dict | None,
) -> dict:
    """Una llamada por slug. ``steps`` ya está resuelto (no manda ``auto``)."""
    steps = dedupe_steps(list(steps))
    if not steps:
        raise LocalFailure("No hay modelo Ollama que ejecutar.", attempts=0, kind="fatal")
    attempts = 0
    chain: list[str] = []
    in_tok = 0
    out_tok = 0
    plan = ""
    final: dict | None = None
    for idx, model in enumerate(steps):
        last = idx == len(steps) - 1
        if len(steps) == 1:
            purpose = "answer"
        else:
            purpose = "code" if last else "plan"
        appendix = f"Plan de diseño:\n{plan}" if purpose == "code" and plan else ""
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
            raise LocalFailure(
                f"Ollama ({model}) no respondió: {exc}",
                attempts=attempts,
                kind=exc.kind or "retry",
            ) from None
        raw = (out.get("text") or "").strip()
        text = visible_answer(raw)
        in_tok += int(out.get("input_tokens") or 0)
        out_tok += int(out.get("output_tokens") or 0)
        if not last:
            plan = raw or text
            continue
        if not response_matches_schema(text, json_schema):
            raise LocalFailure(
                f"Ollama ({model}) respondió vacío o no cumple json_schema.",
                attempts=attempts,
                kind="retry",
            )
        used = out.get("model") if isinstance(out.get("model"), str) else ""
        final = {
            "text": text,
            "provider": "ollama",
            "model_used": (used or model).strip(),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost_usd": 0.0,
            "models_chain": chain,
            "attempts": attempts,
            "key_id": None,
            "key_source": "local",
        }
    if final is None:
        raise LocalFailure("Ollama no produjo respuesta.", attempts=attempts, kind="retry")
    return final
