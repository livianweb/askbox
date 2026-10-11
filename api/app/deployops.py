"""Preferencias de despliegue no secretas (`.askbox/deploy-settings.json`).

`public_url` y `tunnel_enabled` son informativos en la UI y pueden usarse en runtime
para enlaces de reset si no hay `ASKBOX_PUBLIC_URL` en el entorno. El token de Cloudflare
Tunnel vive cifrado en BD (`deploycreds.py`); para levantar `cloudflared` hay que copiarlo
a `CLOUDFLARE_TUNNEL_TOKEN` en `.env` y `docker compose --profile tunnel up -d`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

_PREFS_REL_PATH = os.path.join(".askbox", "deploy-settings.json")
DEFAULT_PREFS = {
    "public_url": "",
    "tunnel_enabled": False,
}
_DEFAULT_PREFS = DEFAULT_PREFS


def _prefs_path(repo_path: str) -> str:
    return os.path.join(repo_path, _PREFS_REL_PATH)


def load_preferences(repo_path: str) -> dict:
    data = dict(_DEFAULT_PREFS)
    try:
        with open(_prefs_path(repo_path), encoding="utf-8") as fh:
            data.update(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return data


def save_preferences(repo_path: str, patch: dict) -> dict:
    path = _prefs_path(repo_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = load_preferences(repo_path)
    merged.update(patch)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return merged


def normalize_public_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def public_url_is_https(url: str) -> bool:
    try:
        return urlparse(normalize_public_url(url)).scheme.lower() == "https"
    except Exception:  # noqa: BLE001
        return False


def effective_public_url(repo_path: str, env_public_url: str) -> str:
    prefs = load_preferences(repo_path)
    from_prefs = normalize_public_url(prefs.get("public_url") or "")
    if from_prefs:
        return from_prefs
    return normalize_public_url(env_public_url) or "http://localhost:8067"


def effective_cookie_secure(repo_path: str, env_cookie_secure: bool, env_public_url: str) -> bool:
    prefs = load_preferences(repo_path)
    url = normalize_public_url(prefs.get("public_url") or "") or normalize_public_url(env_public_url)
    if url and public_url_is_https(url):
        return True
    return env_cookie_secure
