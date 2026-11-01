"""Heurísticas de enrutado. Puras y sin red: las usan la ruta local y allin.

Dos políticas distintas (no mezclar):
- Ollama (`provider=ollama`, modelo omitido o ``auto``): elige modelo local.
  Código habitual → modelo de código; bugs/arquitectura/lógica abstracta → modelo
  de razonamiento; desarrollo complejo (las dos cosas) → plan y luego código.
- Allin: decide qué etapas del híbrido hacen falta. La ejecución vive en ``allin.py``.
"""
from __future__ import annotations

import re

# Frases explícitas de búsqueda o datos actuales. Conservador a propósito:
# «google cloud» o «versión» a secas no disparan la etapa web.
_WEB = re.compile(
    r"(?:"
    r"\bbusca(?:r)?\s+en\s+(?:google|la\s+web|internet)\b"
    r"|\b(?:search|look\s+up)\s+(?:on\s+)?(?:google|the\s+web|internet)\b"
    r"|\bgoogle\s+(?:search|it)\b"
    r"|\blatest\s+version\b"
    r"|\b(?:última|ultima)\s+versi[oó]n\b"
    r"|\bversi[oó]n\s+(?:actual|m[aá]s\s+reciente)\b"
    r"|\bdocumentaci[oó]n\s+reciente\b"
    r"|\brecent\s+(?:docs?|documentation)\b"
    r"|\bhttps?://\S+"
    r")",
    re.IGNORECASE,
)

_HIGH = re.compile(
    r"(?:"
    r"\bdeadlocks?\b"
    r"|\binterbloqueos?\b"
    r"|\barquitectura\b"
    r"|\barchitecture\b"
    r"|\bobscure\s+bugs?\b"
    r"|\bbugs?\s+(?:raros?|oscuros?|intermitentes?|complejos?)\b"
    r"|\bcomplex\s+bugs?\b"
    r"|\bl[oó]gica\s+abstracta\b"
    r"|\babstract\s+logic\b"
    r"|\brace\s+conditions?\b"
    r"|\bcondiciones?\s+de\s+carrera\b"
    r"|\bdesarrollo\s+complejo\b"
    r")",
    re.IGNORECASE,
)

# Código directo, SQL habitual, sintaxis, PrestaShop, maquetación.
# «SELECT the option from the menu» no cuenta: hace falta forma de consulta.
_CODE = re.compile(
    r"(?:"
    r"```"
    r"|\bprestashop\b"
    r"|\bmaquetaci[oó]n\b"
    r"|\bmaqueta(?:r|ción)?\b"
    r"|\bsintaxis\b"
    r"|\bsql\b"
    r"|\bc[oó]digo\b"
    r"|\bSELECT\s+(?:\*|[\w.]+)(?:\s*,\s*(?:\*|[\w.]+)){0,8}\s+FROM\b"
    r"|\bINSERT\s+INTO\b"
    r"|\bUPDATE\s+\w+\s+SET\b"
    r"|\b(?:write|implement|generate)\b.{0,48}\b(?:function|class|script|code|endpoint|api)\b"
    r"|\b(?:implementa|escribe|genera|programa|codifica)\b.{0,48}\b(?:funci[oó]n|clase|c[oó]digo|script|endpoint|api|sql)\b"
    r"|\b(?:arregla|corrige|fix)\b.{0,40}\b(?:bug|c[oó]digo|code|sql|funci[oó]n|function)\b"
    r"|\bdef\s+\w+\s*\("
    r"|\bclass\s+\w+"
    r"|\bfunction\s+\w+\s*\("
    r")",
    re.IGNORECASE | re.DOTALL,
)

_THINK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def prompt_text(messages: list[dict] | None) -> str:
    parts: list[str] = []
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n".join(parts)


def needs_web(prompt: str) -> bool:
    return bool(_WEB.search(prompt or ""))


def is_high_complexity(prompt: str) -> bool:
    return bool(_HIGH.search(prompt or ""))


def needs_code(prompt: str) -> bool:
    return bool(_CODE.search(prompt or ""))


def complex_development(prompt: str) -> bool:
    """Alta dificultad de desarrollo: hay que razonar el plan y además escribir código."""
    return is_high_complexity(prompt) and needs_code(prompt)


def ollama_auto_kind(prompt: str) -> str:
    """``pipeline`` | ``reason`` | ``code``. Solo modelos locales; no implica nube."""
    if complex_development(prompt):
        return "pipeline"
    if is_high_complexity(prompt):
        return "reason"
    return "code"


def allin_shape(prompt: str, *, has_attachments: bool, web_needed: bool, doc_and_web: bool) -> str:
    """``simple`` | ``plan_code`` | ``reason`` | ``code``.

    ``simple``: sin adjuntos, sin web y complejidad baja (una sola llamada local).
    ``doc_and_web``: la extracción y el resumen web aportaron texto (sube la complejidad).
    """
    high = is_high_complexity(prompt) or doc_and_web
    code = needs_code(prompt)
    if not has_attachments and not web_needed and not high:
        return "simple"
    if high and code:
        return "plan_code"
    if high or not code:
        return "reason"
    return "code"


def visible_answer(text: str) -> str:
    """Quita el bloque ``<think>`` de DeepSeek-R1. Si no queda nada, conserva el original."""
    raw = (text or "").strip()
    cleaned = _THINK.sub("", raw).strip()
    return cleaned or raw


def stage_messages(messages: list[dict], *, purpose: str, appendix: str = "") -> list[dict]:
    """Copia de texto (sin adjuntos) con la instrucción de la etapa.

    ``answer`` no añade consigna: la petición del cliente sale tal cual.
    ``plan`` pide diseño, no el código final. ``code`` pide código de producción.
    """
    extra = {
        "answer": "",
        "plan": (
            "Da la lógica de diseño, la resolución y la arquitectura paso a paso. "
            "No hace falta el código final completo."
        ),
        "code": (
            "Genera código de producción limpio y listo para usar, "
            "a partir del contexto y del plan si lo hay."
        ),
    }.get(purpose, "")
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages or []:
        content = msg.get("content")
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            continue
        if (msg.get("role") or "user") == "system":
            system_parts.append(text)
        else:
            user_parts.append(text)
    if extra:
        system_parts.append(extra)
    note = (appendix or "").strip()
    if note:
        user_parts.append(note)
    out: list[dict] = []
    if system_parts:
        out.append({"role": "system", "content": "\n\n".join(system_parts)})
    user = "\n\n".join(user_parts).strip() or "Continúa con la petición."
    out.append({"role": "user", "content": user})
    return out
