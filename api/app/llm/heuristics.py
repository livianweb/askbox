"""Heurísticas de enrutado. Puras y sin red: las usan la ruta local y allin.

Dos políticas distintas (no mezclar):
- Ollama (``provider=ollama``, modelo omitido o ``auto``): elige modelo local.
  Código (defecto) → código; razonamiento duro → razonamiento; texto/NLP → NLP;
  contexto muy largo → contexto largo. Desarrollo complejo (plan y código) →
  las dos llamadas locales. Nunca implica nube.
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
    r"|\bsage\s*x3\b"
    r"|\barquitectura\b"
    r"|\barchitecture\b"
    r"|\bobscure\s+bugs?\b"
    r"|\bbugs?\s+(?:raros?|oscuros?|intermitentes?|complejos?)\b"
    r"|\bcomplex\s+bugs?\b"
    r"|\bcomplex\s+algorithms?\b"
    r"|\balgoritmos?\s+complejos?\b"
    r"|\bl[oó]gica\s+abstracta\b"
    r"|\babstract\s+logic\b"
    r"|\brace\s+conditions?\b"
    r"|\bcondiciones?\s+de\s+carrera\b"
    r"|\bdesarrollo\s+complejo\b"
    r"|\bpaso\s+a\s+paso\b"
    r"|\bstep[- ]by[- ]step\b"
    r"|\bdebug(?:ging|gear)?\b"
    r"|\bdepur(?:a|ar|aci[oó]n)\b"
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
    r"|\brefactor(?:iza(?:r|ción)?|ing|ed)?\b"
    r"|\bphp\b"
    r"|\bdockerfile\b"
    r"|\bdocker-compose(?:\.ya?ml)?\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# El usuario pide escribir o corregir código, no solo menciona un lenguaje.
_CODE_ACTION = re.compile(
    r"(?:"
    r"\b(?:write|implement|generate|refactor)\b"
    r"|\b(?:implementa|escribe|genera|programa|codifica|refactoriza)\b"
    r"|\b(?:arregla|corrige|fix)\b.{0,48}\b(?:bug|c[oó]digo|code|sql|funci[oó]n|function|dockerfile|php)\b"
    r"|\bdockerfile\b"
    r"|\bdocker-compose(?:\.ya?ml)?\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Texto/NLP: clasificar, resumir, categorizar intención. No es el defecto.
_NLP = re.compile(
    r"(?:"
    r"\bclasifica(?:r|ción|d[oa]s?)?\b"
    r"|\bclassify(?:ing)?\b"
    r"|\bcategoriz(?:a|ar|ación|e)\b"
    r"|\bcategori[sz]e\b"
    r"|\b(?:resume|resumen|summarize|summarise)\b.{0,80}\b(?:logs?|tickets?|texto|text|conversaci[oó]n|mensajes?|incidencias?)\b"
    r"|\b(?:logs?|tickets?|incidencias?)\b.{0,80}\b(?:resume|resumen|summarize|summarise|classify|clasifica)\b"
    r"|\btraduc(?:e|ir|ción)\b"
    r"|\btranslate\b"
    r"|\bparafrasea(?:r)?\b"
    r"|\ban[aá]lisis\s+de\s+sentimiento\b"
    r"|\bsentiment\s+analysis\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Contexto largo: ~8k tokens, o un manual / varias fuentes con cuerpo ya grande.
# 4 caracteres ≈ 1 token (estimación barata, sin tokenizador).
LONGCTX_TOKEN_THRESHOLD = 8000
_LONGCTX_HINT_TOKENS = 2000
_CHARS_PER_TOKEN = 4

_LONG_HINT = re.compile(
    r"(?:"
    r"\bmanual(?:es)?\s+de\s+referencia\b"
    r"|\breference\s+manuals?\b"
    r"|\b(?:m[uú]ltiples|varias|several|multiple)\s+(?:fuentes?|documentos?|sources?)\b"
    r"|\blong\s+context\b"
    r"|\bcontexto\s+(?:muy\s+)?largo\b"
    r"|\barchivos?\s+t[eé]cnicos?\s+(?:muy\s+)?largos?\b"
    r")",
    re.IGNORECASE,
)
_SOURCE_MARK = re.compile(
    r"(?im)^(?:#{1,3}\s*)?(?:fuente|documento|source|manual)\s*\d+\s*[:.)\-]",
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


def is_code_action(prompt: str) -> bool:
    """Pide escribir, refactorizar o corregir código (no solo nombra un lenguaje)."""
    return bool(_CODE_ACTION.search(prompt or ""))


def is_nlp(prompt: str) -> bool:
    return bool(_NLP.search(prompt or ""))


def estimate_tokens(text: str) -> int:
    return max(0, len(text or "")) // _CHARS_PER_TOKEN


def is_long_context(prompt: str) -> bool:
    """Lectura masiva: ~>8k tokens, o manual/varias fuentes con ~>2k tokens de cuerpo."""
    text = prompt or ""
    tokens = estimate_tokens(text)
    if tokens >= LONGCTX_TOKEN_THRESHOLD:
        return True
    if tokens < _LONGCTX_HINT_TOKENS:
        return False
    if _LONG_HINT.search(text):
        return True
    return len(_SOURCE_MARK.findall(text)) >= 2


def complex_development(prompt: str) -> bool:
    """Alta dificultad de desarrollo: hay que razonar el plan y además escribir código."""
    return is_high_complexity(prompt) and needs_code(prompt)


def ollama_auto_kind(prompt: str) -> str:
    """``longctx`` | ``nlp`` | ``pipeline`` | ``reason`` | ``code``.

    Solo modelos locales; no implica nube. El código es el defecto.
    El plan+código gana a la lectura larga cuando la tarea es desarrollo complejo.
    """
    long = is_long_context(prompt)
    high = is_high_complexity(prompt)
    code = needs_code(prompt)
    if long and not (high and code):
        return "longctx"
    if high and code:
        return "pipeline"
    if high:
        return "reason"
    if is_nlp(prompt) and not is_code_action(prompt):
        return "nlp"
    return "code"


def allin_single_kind(prompt: str) -> str:
    """Modelo de la llamada única (atajo sin etapas). ``code`` | ``nlp`` | ``longctx`` | ``reason``."""
    if is_long_context(prompt):
        return "longctx"
    if needs_code(prompt):
        return "code"
    if is_nlp(prompt) and not is_code_action(prompt):
        return "nlp"
    return "reason"


def allin_stages(
    prompt: str,
    *,
    has_attachments: bool,
    web_needed: bool,
    doc_and_web: bool,
    context_tokens: int = 0,
) -> dict:
    """Etapas locales de allin, sin ejecutarlas.

    ``shape`` conserva el contrato anterior (``simple`` | ``plan_code`` | ``reason`` | ``code``).
    ``single`` solo en el atajo. ``context`` es la etapa 3 (``reason`` o ``longctx``).
    ``code`` es la etapa 4.
    """
    shape = allin_shape(
        prompt,
        has_attachments=has_attachments,
        web_needed=web_needed,
        doc_and_web=doc_and_web,
    )
    long_read = is_long_context(prompt) or context_tokens >= LONGCTX_TOKEN_THRESHOLD
    if shape == "simple":
        return {"shape": shape, "single": allin_single_kind(prompt), "context": None, "code": False}
    context = None
    code = False
    if shape == "plan_code":
        context = "longctx" if long_read else "reason"
        code = True
    elif shape == "code":
        code = True
        if long_read:
            context = "longctx"
    else:
        context = "longctx" if long_read else "reason"
    return {"shape": shape, "single": None, "context": context, "code": code}


def allin_needs_report(
    *,
    shape: str,
    has_extracted: bool,
    has_web: bool,
    has_plan: bool,
    has_code_stage: bool,
) -> bool:
    """Etapa 5: informe solo si hubo trabajo intermedio sustantivo.

    El atajo de una sola llamada no informa. Un razonamiento o un código sueltos
    tampoco. OCR, web o un plan seguido de código sí.
    """
    if shape == "simple":
        return False
    if has_extracted or has_web:
        return True
    return bool(has_plan and has_code_stage)


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
        "read": (
            "Lee el contexto largo y responde con los datos técnicos relevantes. "
            "Cita procedimientos y restricciones. No hace falta el código final completo."
        ),
        "report": (
            "Redacta el informe final de la tarea. "
            "Conserva el resultado útil (código, SQL o resolución) de forma completa; "
            "no lo sustituyas por un resumen que lo omita. "
            "No inventes datos que no estén en el contexto."
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
