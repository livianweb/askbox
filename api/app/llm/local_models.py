"""Modelos Ollama activos por proyecto. Solo selección de ruta, sin red ni Ollama.

El enrutado automático (cuatro rutas locales y ``mode=allin``) pide un slug.
Si ese slug está activo se usa. Si no, se usa el local principal activo
(por defecto el de código, ``qwen2.5-coder:14b``). Si no queda ninguno activo,
el llamador omite Ollama y sigue por la cascada de nube.

Un ``model`` concreto en ``provider=ollama`` no pasa por aquí: el cliente lo pidió.
Desactivar un modelo no lo borra de Ollama ni cambia ``OLLAMA_MAX_LOADED_MODELS``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import settings
from app.llm.ollama_route import valid_ollama_model

ROLE_LABELS = {
    "code": "Código",
    "reason": "Razonamiento",
    "nlp": "Texto y NLP",
    "longctx": "Contexto largo",
}


class LocalPolicyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def slot_catalog() -> list[dict[str, Any]]:
    """Slots ``OLLAMA_*`` y ``ALLIN_*``. Un mismo slug une los dos orígenes."""
    pairs = (
        ("code", settings.ollama_model),
        ("code", settings.allin_code_model),
        ("reason", settings.ollama_reason_model),
        ("reason", settings.allin_reason_model),
        ("nlp", settings.ollama_nlp_model),
        ("nlp", settings.allin_nlp_model),
        ("longctx", settings.ollama_longctx_model),
        ("longctx", settings.allin_longctx_model),
    )
    order: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for role, raw in pairs:
        slug = (raw or "").strip()
        if not slug or not valid_ollama_model(slug):
            continue
        if slug not in seen:
            seen[slug] = {"slug": slug, "roles": []}
            order.append(slug)
        if role not in seen[slug]["roles"]:
            seen[slug]["roles"].append(role)
    return [seen[slug] for slug in order]


def default_active_slugs() -> set[str]:
    """Default de servidor cuando el proyecto aún no guardó preferencias."""
    raw = (settings.ollama_default_active or "").strip()
    if raw == "*":
        return {item["slug"] for item in slot_catalog()}
    if raw:
        return {part.strip() for part in raw.split(",") if part.strip()}
    return {slug for slug in (settings.ollama_model, settings.allin_code_model) if (slug or "").strip()}


def prefs_from_project(project: dict | None) -> dict:
    if not project:
        return {}
    raw = project.get("ollama_models")
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def is_enabled(slug: str, prefs: dict) -> bool:
    enabled_map = prefs.get("enabled")
    if isinstance(enabled_map, dict) and slug in enabled_map:
        return bool(enabled_map[slug])
    return slug in default_active_slugs()


def enabled_slugs(prefs: dict | None = None) -> list[str]:
    prefs = prefs or {}
    catalog = [item["slug"] for item in slot_catalog()]
    extras: list[str] = []
    enabled_map = prefs.get("enabled")
    if isinstance(enabled_map, dict):
        for slug, on in enabled_map.items():
            if not on or not isinstance(slug, str):
                continue
            text = slug.strip()
            if text and text not in catalog and text not in extras and valid_ollama_model(text):
                extras.append(text)
    return [slug for slug in catalog + extras if is_enabled(slug, prefs)]


def preferred_active(enabled: list[str], prefs: dict | None = None) -> str | None:
    """Principal designado si está activo; si no, el de código / qwen; si no, el primero."""
    if not enabled:
        return None
    prefs = prefs or {}
    enabled_set = set(enabled)
    primary = prefs.get("primary") if isinstance(prefs.get("primary"), str) else ""
    primary = primary.strip()
    if primary and primary in enabled_set:
        return primary
    for candidate in (settings.allin_code_model, settings.ollama_model):
        if candidate and candidate in enabled_set:
            return candidate
    for slug in enabled:
        low = slug.lower()
        if "coder" in low or "qwen" in low:
            return slug
    return enabled[0]


def resolve_local_model(requested: str, project: dict | None) -> str | None:
    """Slug a ejecutar, o None si no hay ningún local activo (toca la nube)."""
    prefs = prefs_from_project(project)
    enabled = enabled_slugs(prefs)
    req = (requested or "").strip()
    if req and req in set(enabled):
        return req
    return preferred_active(enabled, prefs)


def policy_fingerprint(project: dict | None) -> str:
    """Cambia la clave de caché de allin cuando el proyecto activa o desactiva locales."""
    prefs = prefs_from_project(project)
    enabled = enabled_slugs(prefs)
    primary = preferred_active(enabled, prefs) or ""
    blob = primary + "\n" + "\n".join(enabled)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def all_enabled_prefs() -> dict:
    """Preferencia explícita con los cuatro slots activos (tests y escape ``*``)."""
    catalog = slot_catalog()
    primary = preferred_active([item["slug"] for item in catalog], {}) or ""
    return {"primary": primary, "enabled": {item["slug"]: True for item in catalog}}


def public_view(project: dict | None) -> dict:
    prefs = prefs_from_project(project)
    catalog = slot_catalog()
    enabled = enabled_slugs(prefs)
    enabled_set = set(enabled)
    primary = preferred_active(enabled, prefs) if enabled else None
    models = []
    for item in catalog:
        roles = item["roles"]
        models.append({
            "slug": item["slug"],
            "roles": roles,
            "label": " · ".join(ROLE_LABELS.get(role, role) for role in roles),
            "enabled": item["slug"] in enabled_set,
            "primary": item["slug"] == primary,
        })
    return {"primary": primary, "models": models}


def apply_update(
    project: dict | None,
    models: list[dict],
    primary: str | None,
    *,
    primary_set: bool,
) -> dict:
    """Mapa a guardar en ``projects.ollama_models``. Lanza ``LocalPolicyError``."""
    catalog = slot_catalog()
    known = {item["slug"] for item in catalog}
    current = {item["slug"]: is_enabled(item["slug"], prefs_from_project(project)) for item in catalog}
    for item in models:
        slug = (item.get("slug") or "").strip()
        if slug not in known:
            raise LocalPolicyError(
                "unknown_local_model",
                "Ese modelo no está entre los slots locales configurados (OLLAMA_* / ALLIN_*).",
            )
        current[slug] = bool(item.get("enabled"))
    enabled = [item["slug"] for item in catalog if current[item["slug"]]]
    enabled_set = set(enabled)
    prefs = prefs_from_project(project)
    if primary_set:
        chosen = (primary or "").strip()
        if chosen and chosen not in known:
            raise LocalPolicyError(
                "unknown_local_model",
                "El modelo principal no está entre los slots locales configurados.",
            )
        if chosen and chosen not in enabled_set:
            raise LocalPolicyError(
                "primary_inactive",
                "El modelo principal tiene que estar activo.",
            )
        if not chosen:
            chosen = preferred_active(enabled, {"primary": ""}) or ""
    else:
        stored = prefs.get("primary") if isinstance(prefs.get("primary"), str) else ""
        stored = stored.strip()
        if stored and stored in enabled_set:
            chosen = stored
        else:
            chosen = preferred_active(enabled, {"primary": ""}) or ""
    return {
        "primary": chosen,
        "enabled": {item["slug"]: bool(current[item["slug"]]) for item in catalog},
    }
