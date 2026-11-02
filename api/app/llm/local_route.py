"""Perfiles fijos para el 7B local. La nube solo elige el id.

sanitize (clawBox) aplica la decisión. Un timeout o un fallo de nube
devuelve el perfil lean para no frenar Ollama.

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

# Prefijo estable. Los datos del turno van solo en el mensaje user.
ROUTER_SYSTEM = (
    "Route one turn for a local 7B coder. "
    "Pick exactly one profile id from the allowed list. "
    "lean = trivial question. standard = short follow-up. full = long task or code. "
    'Reply JSON only: {"profile":"<id>","reason":"<=12 words"}.'
)

_PROFILE_WORD = re.compile(r"\b(lean|standard|full)\b", re.I)
_FALLBACK_REASONS = frozenset({"timeout", "cloud_unavailable", "invalid_profile", "rate_limited"})


def fallback_id(allowed: list[str]) -> str:
    """El más corto entre los permitidos. Timeout sin lean sigue siendo el más corto."""
    for name in PROFILE_IDS:
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


def build_messages(signals: dict[str, Any], allowed: list[str]) -> list[dict[str, str]]:
    last = " ".join(str(signals.get("last_user") or "").split())
    if len(last) > 1200:
        last = last[:1200]
    payload = {
        "last_user": last,
        "n_messages": int(signals.get("n_messages") or 0),
        "approx_chars": int(signals.get("approx_chars") or 0),
        "has_code": bool(signals.get("has_code")),
        "has_tools": bool(signals.get("has_tools")),
        "profiles": list(allowed),
    }
    return [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
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
