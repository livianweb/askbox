"""Ruta explícita a Ollama o a DeepSeek nube. No entra en la cascada cruzada.

Si el cliente no pide Ollama ni un id DeepSeek, `parse_explicit_provider`
devuelve None y el router sigue con la cascada (Gemini, OpenRouter y DeepSeek
si hay clave). ``provider=allin`` no se resuelve aquí (lo hace el orquestador
híbrido). Sin modelo, o con modelo ``auto``, Ollama devuelve ``auto`` y el
router elige en local. DeepSeek sin modelo devuelve ``deepseek-chat``.

El tag local ``deepseek-r1:14b`` no es el proveedor ``deepseek``.
"""
from __future__ import annotations

import re

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
# ollama-safe/ antes que ollama/: el segundo es prefijo del primero.
_PREFIXES = ("ollama-safe/", "ollama/", "askbox/", "deepseek/")

# Ids de la API nativa. No incluir deepseek-r1:14b (eso es Ollama).
DEEPSEEK_MODELS = frozenset({"deepseek-chat", "deepseek-reasoner"})
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"


def _peel_prefixes(raw: str) -> tuple[list[str], str]:
    text = (raw or "").strip()
    peeled: list[str] = []
    while text:
        low = text.lower()
        match = next((p for p in _PREFIXES if low.startswith(p)), None)
        if not match:
            break
        peeled.append(match[:-1])
        text = text[len(match) :].strip()
    return peeled, text


def _looks_like_ollama_model(slug: str) -> bool:
    """Un tag `nombre:versión` es modelo local. `cloud-fast` es un tier de nube."""
    return ":" in (slug or "") and valid_ollama_model(slug)


def strip_ollama_prefix(raw: str) -> tuple[bool, str]:
    """Quita `ollama/`, `ollama-safe/` y `askbox/` (sin distinguir mayúsculas).

    El bool es True si eso elige la ruta Ollama: prefijo `ollama/` u
    `ollama-safe/`, o `askbox/` cuyo resto es un modelo con tag (`qwen:7b`).
    `askbox/cloud-fast` se limpia en el slug pero no marca la ruta.
    """
    peeled, rest = _peel_prefixes(raw)
    if not peeled:
        return False, rest
    if any(name in ("ollama", "ollama-safe") for name in peeled):
        return True, rest
    if "askbox" in peeled and _looks_like_ollama_model(rest):
        return True, rest
    return False, rest


def valid_ollama_model(slug: str) -> bool:
    if not slug or ".." in slug:
        return False
    if slug.startswith((".", "/")) or slug.endswith("/"):
        return False
    return bool(_MODEL_RE.match(slug))


def canonical_deepseek_model(slug: str) -> str | None:
    """Id oficial en minúsculas, o None si no es chat ni reasoner."""
    low = (slug or "").strip().lower()
    if low in DEEPSEEK_MODELS:
        return low
    return None


def parse_explicit_provider(provider: str | None, model: str | None) -> tuple[str, str] | None:
    """None = cascada. ('ollama', slug) local. ('deepseek', slug) solo DeepSeek.

    Acepta `provider=ollama` (modelo opcional) o `model=ollama/<slug>`,
    `ollama-safe/<slug>` y `askbox/<nombre:tag>` (el prefijo implica Ollama
    y el slug sale limpio). `askbox/cloud-fast` no es ruta local.
    Sin modelo, o con ``auto``, el slug de Ollama es ``auto``.

    DeepSeek: `provider=deepseek` (default `deepseek-chat`), `model=deepseek-chat`
    o `deepseek-reasoner` (aunque no venga provider), y el prefijo `deepseek/`
    o `askbox/deepseek-chat`. Un fallo de esa ruta no vuelve a la cascada.
    `deepseek-r1:14b` sigue siendo Ollama.

    Combinaciones inválidas lanzan ValueError. ``allin`` no se acepta aquí.
    """
    prov = (provider or "").strip().lower() or None
    raw_model = (model or "").strip() or None
    if prov is None and raw_model is None:
        return None

    prefixed, slug_from_prefix = strip_ollama_prefix(raw_model or "")
    if prefixed:
        if prov not in (None, "ollama"):
            raise ValueError("El modelo con prefijo local no coincide con provider.")
        if slug_from_prefix.lower() == "auto":
            return ("ollama", "auto")
        if not valid_ollama_model(slug_from_prefix):
            raise ValueError("Modelo Ollama no válido.")
        return ("ollama", slug_from_prefix)

    slug = slug_from_prefix if raw_model else ""
    deepseek_slug = canonical_deepseek_model(slug)
    if deepseek_slug:
        if prov not in (None, "deepseek"):
            raise ValueError('El modelo DeepSeek no coincide con provider. Usa "deepseek" u omítelo.')
        return ("deepseek", deepseek_slug)

    if prov == "deepseek":
        if not slug or slug.lower() in {"auto", "deepseek"}:
            return ("deepseek", DEEPSEEK_DEFAULT_MODEL)
        raise ValueError("Modelo DeepSeek no válido. Usa deepseek-chat o deepseek-reasoner.")

    if prov == "ollama":
        if raw_model is None or raw_model.lower() == "auto":
            return ("ollama", "auto")
        if not valid_ollama_model(slug):
            raise ValueError("Modelo Ollama no válido.")
        return ("ollama", slug)

    if prov is None:
        raise ValueError(
            'model solo se admite con provider "ollama" o "deepseek", '
            "con prefijo ollama/, ollama-safe/, deepseek/ o askbox/<modelo:tag>, "
            "o con el id deepseek-chat / deepseek-reasoner."
        )
    raise ValueError(
        'provider explícito no soportado. Usa "ollama", "deepseek", "allin" u omítelo para la cascada.'
    )
