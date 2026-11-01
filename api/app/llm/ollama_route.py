"""Ruta explícita a Ollama. No entra en la cascada de model_catalog.

Si el cliente no pide Ollama, `parse_explicit_provider` devuelve None y el
router sigue con Gemini/OpenRouter. ``provider=allin`` no se resuelve aquí
(lo hace el orquestador híbrido). Sin modelo, o con modelo ``auto``, el
slug devuelto es ``auto`` y el router elige Qwen/DeepSeek en local.
"""
from __future__ import annotations

import re

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def strip_ollama_prefix(raw: str) -> tuple[bool, str]:
    """Si `raw` empieza por `ollama/`, devuelve el slug sin ese prefijo."""
    text = (raw or "").strip()
    if text.lower().startswith("ollama/"):
        return True, text.split("/", 1)[1].strip()
    return False, text


def valid_ollama_model(slug: str) -> bool:
    if not slug or ".." in slug:
        return False
    if slug.startswith((".", "/")) or slug.endswith("/"):
        return False
    return bool(_MODEL_RE.match(slug))


def parse_explicit_provider(provider: str | None, model: str | None) -> tuple[str, str] | None:
    """None = cascada actual. ('ollama', slug) = ruta 100 % local.

    Acepta `provider=ollama` (modelo opcional) o `model=ollama/<slug>`
    (el prefijo implica el proveedor). Sin modelo, o con ``auto``, el slug es
    ``auto`` (selección local en el router, no un nombre de modelo de Ollama).
    Combinaciones inválidas lanzan ValueError. ``allin`` no se acepta aquí.
    """
    prov = (provider or "").strip().lower() or None
    raw_model = (model or "").strip() or None
    if prov is None and raw_model is None:
        return None

    prefixed, slug_from_prefix = strip_ollama_prefix(raw_model or "")
    if prefixed:
        if prov not in (None, "ollama"):
            raise ValueError('El modelo con prefijo ollama/ no coincide con provider.')
        if slug_from_prefix.lower() == "auto":
            return ("ollama", "auto")
        if not valid_ollama_model(slug_from_prefix):
            raise ValueError("Modelo Ollama no válido.")
        return ("ollama", slug_from_prefix)

    if prov == "ollama":
        if raw_model is None or raw_model.lower() == "auto":
            return ("ollama", "auto")
        slug = strip_ollama_prefix(raw_model)[1]
        if not valid_ollama_model(slug):
            raise ValueError("Modelo Ollama no válido.")
        return ("ollama", slug)

    if prov is None:
        raise ValueError('model solo se admite con provider "ollama" o con prefijo ollama/.')
    raise ValueError('provider explícito no soportado. Usa "ollama", "allin" u omítelo para la cascada.')
