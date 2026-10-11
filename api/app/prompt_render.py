"""Sustitución de variables {{name}} en plantillas de prompt."""
from __future__ import annotations

import re

_VAR = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


class PromptRenderError(ValueError):
    pass


def extract_variables(template: str) -> set[str]:
    return set(_VAR.findall(template or ""))


def render_template(template: str, variables: dict[str, str] | None) -> str:
    variables = variables or {}
    missing = extract_variables(template) - set(variables.keys())
    if missing:
        raise PromptRenderError(f"Faltan variables: {', '.join(sorted(missing))}")
    return _VAR.sub(lambda m: variables[m.group(1)], template)
