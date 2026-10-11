"""Configuración desde variables de entorno (sin secretos en código)."""
from __future__ import annotations

import os
from pathlib import Path


def _default_repo_path() -> str:
    # api/app/config.py -> api/app -> api -> askbox (raíz local si se ejecuta fuera de Docker;
    # dentro del contenedor por defecto no existe .git ahí y la pantalla de ajustes Git lo informa).
    return str(Path(__file__).resolve().parents[2])


def _parse_repo_map(raw: str) -> dict:
    out: dict = {}
    for item in (raw or "").split(","):
        name, sep, path = item.strip().partition("=")
        if sep and name.strip() and path.strip():
            out[name.strip()] = path.strip()
    return out


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # Cifrado de claves de proveedor (AES-256-GCM). base64 o hex de 32 bytes.
    master_key: str = os.getenv("ASKBOX_MASTER_KEY", "")

    # Claves de sistema (opcionales, solo servidor; nunca salen por la API).
    system_gemini_key: str = os.getenv("GEMINI_API_KEY", "")
    system_openrouter_key: str = os.getenv("OPENROUTER_API_KEY", "")

    # Auth
    cookie_name: str = "askbox_session"
    cookie_secure: bool = _bool("ASKBOX_COOKIE_SECURE", False)
    session_ttl_seconds: int = _int("ASKBOX_SESSION_TTL_SECONDS", 60 * 60 * 24 * 30)
    allow_register: bool = _bool("ASKBOX_ALLOW_REGISTER", False)  # mono-usuario: solo el primero

    # Recuperación de contraseña
    public_url: str = (os.getenv("ASKBOX_PUBLIC_URL") or "http://localhost:8067").rstrip("/")
    # Prefijo público de la API vía proxy web (/api/* → API). Vacío si accedes directo a :8066.
    api_external_prefix: str = (os.getenv("ASKBOX_API_EXTERNAL_PREFIX") or "").rstrip("/")
    reset_ttl_minutes: int = _int("ASKBOX_RESET_TTL_MINUTES", 30)
    # Solo desarrollo local: devuelve reset_url en la respuesta de /forgot (NUNCA en producción).
    dev_show_reset_token: bool = _bool("ASKBOX_DEV_SHOW_RESET_TOKEN", False)
    # Sin SMTP: escribe el enlace de reset en los logs del servidor (instancia mono-usuario).
    log_reset_link: bool = _bool("ASKBOX_LOG_RESET_LINK", True)
    smtp_host: str = os.getenv("ASKBOX_SMTP_HOST", "")
    smtp_port: int = _int("ASKBOX_SMTP_PORT", 587)
    smtp_user: str = os.getenv("ASKBOX_SMTP_USER", "")
    smtp_password: str = os.getenv("ASKBOX_SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("ASKBOX_SMTP_FROM", "")
    smtp_tls: str = (os.getenv("ASKBOX_SMTP_TLS") or "starttls").lower()  # starttls | ssl | none

    # Límites
    max_prompt_chars: int = _int("ASKBOX_MAX_PROMPT_CHARS", 20000)
    max_system_chars: int = _int("ASKBOX_MAX_SYSTEM_CHARS", 4000)
    max_output_tokens_cap: int = _int("ASKBOX_MAX_OUTPUT_TOKENS_CAP", 8192)
    ask_rate_per_min: int = _int("ASKBOX_ASK_RATE_PER_MIN", 30)
    batch_max_items: int = _int("ASKBOX_BATCH_MAX_ITEMS", 32)
    login_rate_per_min: int = _int("ASKBOX_LOGIN_RATE_PER_MIN", 10)

    # LLM
    provider_timeout: float = float(os.getenv("ASKBOX_PROVIDER_TIMEOUT", "30") or 30)
    max_models_per_provider: int = _int("ASKBOX_MAX_MODELS_PER_PROVIDER", 3)
    openrouter_referer: str = os.getenv("ASKBOX_OPENROUTER_REFERER", "https://askbox.local")
    openrouter_title: str = os.getenv("ASKBOX_OPENROUTER_TITLE", "askbox")

    # Pantalla de configuración Git (ver app/gitops.py). Ruta del repo accesible desde este
    # proceso; vacío/no accesible => la pantalla lo informa en vez de fallar o simular datos.
    repo_path: str = os.getenv("ASKBOX_REPO_PATH", "") or _default_repo_path()
    # Repos adicionales gestionables desde /settings/git (p. ej. el kit de orquestación):
    # "nombre=/ruta,otro=/ruta2". Solo repos montados explícitamente (ver docker-compose.yml).
    extra_repos: dict = _parse_repo_map(os.getenv("ASKBOX_EXTRA_REPOS", ""))


settings = Settings()
