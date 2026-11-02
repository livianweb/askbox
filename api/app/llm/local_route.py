"""Perfiles fijos para el 7B local. La nube solo elige el id.

sanitize (clawBox) aplica la decisión. Timeout, nube caída o rate limit
devuelven standard (keep=4). Lean solo si la nube lo elige.

Tuplas congeladas (dentro del contrato):
  lean     keep=2  system=lean     max_out=96   (rango 64–128)
  standard keep=4  system=summary  max_out=128
  full     keep=12 system=full     max_out=256  (keep dentro de 8–12)
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.llm.schema_check import extract_json_value

PROFILE_IDS = ("lean", "standard", "full")

# Una tupla por id: el prefijo del 7B no cambia dentro del mismo perfil.
PROFILES: dict[str, dict[str, Any]] = {
    "lean": {"keep_messages": 2, "system": "lean", "max_out": 96},
    "standard": {"keep_messages": 4, "system": "summary", "max_out": 128},
    "full": {"keep_messages": 12, "system": "full", "max_out": 256},
}

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["profile"],
    "properties": {
        "profile": {"type": "string"},
        "reason": {"type": "string"},
    },
}

# Una línea fija. El turno va en el user, recortado.
ROUTER_SYSTEM = 'JSON {"profile":"lean|standard|full","reason":"w"}.'

ROUTE_MAX_TOKENS = 32

_PROFILE_WORD = re.compile(r"\b(lean|standard|full)\b", re.I)
_FALLBACK_REASONS = frozenset({"timeout", "cloud_unavailable", "invalid_profile", "rate_limited"})


def fallback_id(allowed: list[str]) -> str:
    """standard (keep=4) si está permitido. Luego full. Lean solo si no hay otro."""
    for name in ("standard", "full", "lean"):
        if name in allowed:
            return name
    return allowed[0]


def pack(profile: str, reason: str) -> dict[str, Any]:
    row = PROFILES[profile]
    text = " ".join((reason or "").split())
    if not text:
        text = profile
    return {
        "profile": profile,
        "keep_messages": int(row["keep_messages"]),
        "system": row["system"],
        "max_out": int(row["max_out"]),
        "reason": text[:160],
    }


def gemini_speed_rank(slug: str) -> tuple:
    """Menor tupla = se prueba antes. flash-lite, luego flash, luego 8b."""
    s = (slug or "").lower()
    if "flash-lite" in s or "flashlite" in s:
        family = 0
    elif "8b" in s:
        family = 2
    elif "flash" in s:
        family = 1
    elif "lite" in s:
        family = 3
    else:
        family = 9
    nums = [int(n) for n in re.findall(r"\d+", s)]
    major = nums[0] if nums else 99
    minor = nums[1] if len(nums) > 1 else 0
    return (family, major, minor, len(s), s)


def order_route_models(slugs: list[str]) -> list[str]:
    """Orden de prueba. Los 404 se descartan fuera, no aquí."""
    usable: list[str] = []
    seen: set[str] = set()
    for raw in slugs:
        slug = (raw or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        usable.append(slug)
    return sorted(usable, key=gemini_speed_rank)


def pick_fastest_gemini(slugs: list[str]) -> str | None:
    ordered = order_route_models(slugs)
    return ordered[0] if ordered else None


def build_messages(signals: dict[str, Any], allowed: list[str]) -> list[dict[str, str]]:
    last = " ".join(str(signals.get("last_user") or "").split())
    if len(last) > 160:
        last = last[:160]
    user = (
        f"n={int(signals.get('n_messages') or 0)} "
        f"c={int(signals.get('approx_chars') or 0)} "
        f"code={1 if signals.get('has_code') else 0} "
        f"tools={1 if signals.get('has_tools') else 0} "
        f"ok={','.join(allowed)} u={last}"
    )
    return [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_choice(text: str, allowed: list[str]) -> tuple[str, str] | None:
    """Devuelve (profile, reason) si el id está permitido."""
    allowed_set = set(allowed)
    profile = ""
    reason = ""
    try:
        data = extract_json_value(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        profile = str(data.get("profile") or "").strip().lower()
        reason = str(data.get("reason") or "")
    elif isinstance(data, str):
        profile = data.strip().lower()
    if profile not in allowed_set:
        match = _PROFILE_WORD.search(text or "")
        profile = match.group(1).lower() if match else ""
        reason = reason or ""
    if profile not in allowed_set:
        return None
    return profile, reason


def is_fallback_reason(reason: str) -> bool:
    return reason in _FALLBACK_REASONS
