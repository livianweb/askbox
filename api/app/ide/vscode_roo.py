"""Playbook VS Code + Roo Code. Una sola fuente para el JSON de GET /v1/ide/vscode-roo.

El markdown empaquetado (vscode_roo.md) es el mismo fichero que docs/ide/vscode-roo.md.
La imagen Docker solo copia api/app/, así que el endpoint no depende del montaje /repo.
"""
from __future__ import annotations

import os
from pathlib import Path

PLAYBOOK_ID = "vscode-roo"
PLAYBOOK_VERSION = "1"
TOKEN_PLACEHOLDER = "${SANITIZE_CLIENT_TOKEN}"
DEFAULT_PROFILE = "askbox-cloud-strong"
ROO_SETTINGS_PATH = "~/.roo/askbox-roo-settings.json"
EXTENSION_ID = "RooVeterinaryInc.roo-cline"

# Perfiles que ya usa Roo en el ASUS. cloud-balanced existe en /v1/models
# pero no forma parte de este import: se clona un perfil cloud si hace falta.
PROFILES: list[dict] = [
    {
        "id": "askbox-cloud-fast",
        "model": "cloud-fast",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "kind": "cloud",
        "tier": "fast",
        "notes": (
            "Gemini vía askBox (cascada). El prompt sale del servidor hacia el proveedor. "
            "Sirve para turnos cortos."
        ),
    },
    {
        "id": "askbox-cloud-strong",
        "model": "cloud-strong",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "kind": "cloud",
        "tier": "strong",
        "default": True,
        "notes": (
            "Perfil de arranque. Gemini, tier strong de la cascada askBox. "
            "Misma base y el mismo Bearer que el resto."
        ),
    },
    {
        "id": "askbox-local-qwen7b",
        "model": "local-qwen7b",
        "context_window": 32768,
        "max_output_tokens": 4096,
        "kind": "local",
        "ollama_tag": "qwen2.5-coder-7b-roo",
        "notes": (
            "Ollama exclusivo en vps4 (tag qwen2.5-coder-7b-roo). Sin fallback Gemini. "
            "Sanitize convierte el JSON/XML de texto en tool_calls OpenAI. "
            "Keepalives SSE cada 10 s. Ventana 32k y salida máxima 4096. "
            "Unos 7 GB de RAM; en CPU ARM 4c sin GPU el primer turno puede tardar minutos."
        ),
    },
]


def markdown_text() -> str:
    """Markdown del playbook. Primero el fichero junto a este módulo (imagen Docker)."""
    packaged = Path(__file__).with_name("vscode_roo.md")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo = Path(__file__).resolve().parents[3] / "docs" / "ide" / "vscode-roo.md"
    return repo.read_text(encoding="utf-8")


def public_web_origin() -> str:
    return (os.getenv("ASKBOX_PUBLIC_URL") or "http://localhost:8067").strip().rstrip("/")


def openai_base_url() -> str:
    """Base OpenAI-compatible (termina en /v1). Roo le añade /chat/completions."""
    override = (os.getenv("ASKBOX_IDE_OPENAI_BASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    return f"{public_web_origin()}/v1"


def _roo_profile(profile: dict, base_url: str) -> dict:
    return {
        "id": profile["id"],
        "apiProvider": "openai",
        "openAiBaseUrl": base_url,
        "openAiApiKey": TOKEN_PLACEHOLDER,
        "openAiModelId": profile["model"],
        "openAiStreamingEnabled": True,
        "includeMaxTokens": True,
        "modelMaxTokens": profile["max_output_tokens"],
        "openAiCustomModelInfo": {
            "contextWindow": profile["context_window"],
            "maxTokens": profile["max_output_tokens"],
            "supportsImages": False,
            "supportsPromptCache": False,
        },
    }


def roo_import(base_url: str | None = None) -> dict:
    """JSON que Roo importa (autoImportSettingsPath). La clave es un placeholder."""
    base = base_url or openai_base_url()
    api_configs = {p["id"]: _roo_profile(p, base) for p in PROFILES}
    return {
        "providerProfiles": {
            "currentApiConfigName": DEFAULT_PROFILE,
            "apiConfigs": api_configs,
        },
        "globalSettings": {"codebaseIndexEnabled": False},
    }


def steps(base_url: str) -> list[dict]:
    return [
        {
            "id": "ensure-ripgrep",
            "action": "ensure_ripgrep",
            "command": "rg",
            "when": "vscode portable en Linux",
            "hint": (
                "Si `rg --version` falla en la misma shell que lanza code, "
                "symlink de ripgrep-universal (dentro del portable) a un directorio "
                "del PATH de ese proceso, p. ej. ~/.local/bin/rg."
            ),
        },
        {
            "id": "install-roo",
            "action": "install_extension",
            "extension_id": EXTENSION_ID,
            "open_vsx": "https://open-vsx.org/api/RooVeterinaryInc/roo-cline/latest",
            "command": f"code --install-extension {EXTENSION_ID}",
            "do_not_install": ["Continue.continue"],
            "notes": (
                "VSIX de Open VSX si el marketplace no responde. "
                "Continue llama a POST /v1/completions y esta base responde 404."
            ),
        },
        {
            "id": "read-client-token",
            "action": "read_env",
            "env": "SANITIZE_CLIENT_TOKEN",
            "never_use_env": ["ASKBOX_TOKEN", "ASKBOX_ACCESS_TOKEN", "ASKBOX_MASTER_KEY"],
            "notes": (
                "Token de cliente sanitize. No lo imprimas, no lo pegues en el chat "
                "y no lo escribas en el repo. Roo no expande ${...}: sustituye el valor al escribir el JSON."
            ),
        },
        {
            "id": "verify-models",
            "action": "http_get",
            "url": f"{base_url}/models",
            "header": {"Authorization": f"Bearer {TOKEN_PLACEHOLDER}"},
            "expect_model_ids": ["cloud-fast", "cloud-balanced", "cloud-strong", "local-qwen7b"],
        },
        {
            "id": "write-roo-settings",
            "action": "write_json",
            "path": ROO_SETTINGS_PATH,
            "body": "roo_import",
            "substitute": {"openAiApiKey": "valor de SANITIZE_CLIENT_TOKEN"},
            "mode": "0600",
            "notes": "No commitear. El placeholder del playbook no es una clave válida.",
        },
        {
            "id": "set-auto-import",
            "action": "merge_vscode_settings",
            "setting": "roo-cline.autoImportSettingsPath",
            "value": ROO_SETTINGS_PATH,
            "candidate_files": [
                "${VSCODE_PORTABLE}/user-data/User/settings.json",
                "~/.config/Code/User/settings.json",
                "~/.config/Code - OSS/User/settings.json",
            ],
            "notes": "Conserva el resto de claves. ~/ lo resuelve Roo al activarse.",
        },
        {
            "id": "reload-window",
            "action": "reload_window",
            "vscode_command": "workbench.action.reloadWindow",
            "ui": "Developer: Reload Window",
        },
        {
            "id": "select-profile",
            "action": "select_api_config",
            "name": DEFAULT_PROFILE,
            "local_alternative": "askbox-local-qwen7b",
            "notes": (
                "El import deja askbox-cloud-strong. "
                "Sin model forzado (mode=allin o perfil vacío) y con "
                "OLLAMA_DEFAULT_ACTIVE_MODELS=none la cascada es Gemini→OpenRouter, no Qwen."
            ),
        },
    ]


def playbook() -> dict:
    """JSON estable. Sin secretos: la clave es el placeholder ${SANITIZE_CLIENT_TOKEN}."""
    origin = public_web_origin()
    base = openai_base_url()
    md = markdown_text()
    return {
        "playbook": PLAYBOOK_ID,
        "version": PLAYBOOK_VERSION,
        "locale": "es",
        "base_url": base,
        "auth_header": {
            "name": "Authorization",
            "scheme": "Bearer",
            "template": f"Bearer {TOKEN_PLACEHOLDER}",
            "secret_env": "SANITIZE_CLIENT_TOKEN",
            "never_use_env": ["ASKBOX_TOKEN", "ASKBOX_ACCESS_TOKEN", "ASKBOX_MASTER_KEY"],
        },
        "urls": {
            "openai_base": base,
            "playbook_json": f"{origin}/v1/ide/vscode-roo",
            "playbook_json_api_prefix": f"{origin}/api/v1/ide/vscode-roo",
            "playbook_markdown": f"{origin}/v1/ide/vscode-roo.md",
            "docs_repo": "docs/ide/vscode-roo.md",
            "api_path": "/v1/ide/vscode-roo",
        },
        "markdown_url": f"{origin}/v1/ide/vscode-roo.md",
        "markdown": md,
        "extension": {
            "id": EXTENSION_ID,
            "publisher_view": "Roo Code",
            "open_vsx": "https://open-vsx.org/api/RooVeterinaryInc/roo-cline/latest",
            "avoid": "Continue.continue",
        },
        "profiles": PROFILES,
        "models": [
            {
                "id": "cloud-fast",
                "upstream": "gemini",
                "tier": "fast",
                "fallback": "cascada askBox",
            },
            {
                "id": "cloud-balanced",
                "upstream": "gemini",
                "tier": "balanced",
                "fallback": "cascada askBox",
                "in_default_roo_profiles": False,
            },
            {
                "id": "cloud-strong",
                "upstream": "gemini",
                "tier": "strong",
                "fallback": "cascada askBox",
            },
            {
                "id": "local-qwen7b",
                "upstream": "ollama",
                "ollama_tag": "qwen2.5-coder-7b-roo",
                "fallback": "none",
                "tool_calls": "sanitize emula JSON/XML de texto a tool_calls OpenAI",
                "sse_keepalive_seconds": 10,
            },
        ],
        "default_profile": DEFAULT_PROFILE,
        "routing_note": (
            "mode=allin o una petición sin model, con OLLAMA_DEFAULT_ACTIVE_MODELS=none, "
            "no usa Qwen: cascada Gemini y luego OpenRouter. "
            "El perfil askbox-local-qwen7b envía model=local-qwen7b."
        ),
        "paths": {
            "roo_settings": ROO_SETTINGS_PATH,
            "vscode_setting": "roo-cline.autoImportSettingsPath",
        },
        "roo_import": roo_import(base),
        "steps": steps(base),
        "checklist": [
            {"id": "rg", "pass_if": "rg --version en el PATH del proceso de code"},
            {"id": "extension", "pass_if": f"{EXTENSION_ID} instalada; Continue no hace de cliente"},
            {"id": "token", "pass_if": "GET {base_url}/models devuelve 200 con el Bearer de SANITIZE_CLIENT_TOKEN"},
            {"id": "settings-file", "pass_if": f"{ROO_SETTINGS_PATH} existe, modo 0600, openAiApiKey sustituida"},
            {"id": "auto-import", "pass_if": "roo-cline.autoImportSettingsPath apunta a ese JSON"},
            {"id": "reloaded", "pass_if": "Reload Window hecho y Roo muestra askbox-cloud-strong"},
            {"id": "local-optional", "pass_if": "si piden Qwen, el perfil activo es askbox-local-qwen7b"},
        ],
        "warm_local": {
            "where": "vps4 (Ollama), no el portátil del IDE",
            "model_tag": "qwen2.5-coder-7b-roo",
            "keep_alive": "30m",
            "env": "OLLAMA_KEEP_ALIVE",
            "hint": (
                "curl -sS http://127.0.0.1:11434/api/generate -H 'Content-Type: application/json' "
                "-d '{\"model\":\"qwen2.5-coder-7b-roo\",\"keep_alive\":\"30m\",\"prompt\":\"ok\",\"stream\":false}'"
            ),
        },
        "troubleshooting": [
            {"symptom": "tool_calls incompletos", "cause": "salida local cortada en 4096 o JSON/XML a medias; sanitize solo promueve un bloque entero"},
            {"symptom": "413", "cause": "historial de Roo por encima del tope de cuerpo; tarea nueva"},
            {"symptom": "minutos sin tokens", "cause": "qwen frío o fuera de RAM en CPU ARM; calentar; el SSE manda keepalive cada 10 s"},
            {"symptom": "responde Gemini y no Qwen", "cause": "perfil cloud, o petición sin model con locales en none"},
            {"symptom": "404 /v1/completions", "cause": "Continue, no Roo"},
        ],
    }
