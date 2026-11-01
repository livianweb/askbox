"""Validación mínima de JSON frente a un schema (sin dependencia externa)."""
from __future__ import annotations

import json
import re
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


def extract_json_value(text: str) -> Any:
    """Parse JSON from raw model text; tolerate markdown fences / leading prose."""
    raw = str(text or "").strip()
    if not raw:
        raise json.JSONDecodeError("empty", raw, 0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), count=1)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
    start_obj, end_obj = raw.find("{"), raw.rfind("}")
    start_arr, end_arr = raw.find("["), raw.rfind("]")
    candidates = []
    if start_obj >= 0 and end_obj > start_obj:
        candidates.append(raw[start_obj : end_obj + 1])
    if start_arr >= 0 and end_arr > start_arr:
        candidates.append(raw[start_arr : end_arr + 1])
    for chunk in candidates:
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("no json", raw, 0)


def response_matches_schema(text: str, schema: dict | None) -> bool:
    if not schema:
        return bool((text or "").strip())
    try:
        data = extract_json_value(text)
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
