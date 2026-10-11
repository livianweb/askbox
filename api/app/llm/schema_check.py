"""Validación mínima de JSON frente a un schema (sin dependencia externa)."""
from __future__ import annotations

import json
from typing import Any


def _type_ok(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True


def response_matches_schema(text: str, schema: dict | None) -> bool:
    if not schema:
        return bool((text or "").strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    st = schema.get("type")
    if st and not _type_ok(data, st):
        return False
    if isinstance(data, dict):
        for key in schema.get("required") or []:
            if key not in data:
                return False
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key not in data or not isinstance(sub, dict):
                continue
            sub_type = sub.get("type")
            if sub_type and not _type_ok(data[key], sub_type):
                return False
    return True
