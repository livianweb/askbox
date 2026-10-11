"""Operaciones git de solo lectura / gestión ligera para la pantalla de configuración Git.

Este módulo **no** ejecuta commit ni push por sí mismo: esa decisión la toma el agente/operador
con acceso al host siguiendo `.cursor/skills/git-autopilot`. Aquí solo exponemos estado del
repo y preferencias no secretas (ver `no-secret-leak.mdc`: nunca URLs con credenciales
embebidas, nunca contenido de archivos del repo salvo el propio archivo de preferencias).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

_URL_CREDS = re.compile(r"://[^@/\s]+@")
_PREFS_REL_PATH = os.path.join(".askbox", "git-settings.json")
_DEFAULT_PREFS = {
    "auto_commit": True,
    "remote_name": "origin",
    "default_visibility": "private",
    "last_push_at": None,
}


def default_repo_path() -> str:
    """Raíz del repo si este proceso corre fuera de Docker (api/app/gitops.py -> askbox/)."""
    return str(Path(__file__).resolve().parents[2])


def is_repo_available(repo_path: str) -> bool:
    return bool(repo_path) and os.path.isdir(os.path.join(repo_path, ".git"))


def sanitize_remote_url(url: str) -> str:
    """Quita credenciales embebidas (usuario:token@) antes de mostrar una URL remota."""
    if not url:
        return url
    return _URL_CREDS.sub("://", url)


def _run_git(repo_path: str, *args: str, timeout: float = 5.0) -> tuple[str | None, str | None]:
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, *args], capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if res.returncode != 0:
        return None, (res.stderr or res.stdout).strip()[:300]
    return res.stdout.strip(), None


def parse_remotes(raw: str | None) -> list[dict]:
    """'origin\thttps://u:p@h/x.git (fetch)\n...' -> metadatos con URL saneada, sin duplicados."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in (raw or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        kind = parts[2].strip("()") if len(parts) > 2 else "fetch"
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "url": sanitize_remote_url(url), "type": kind})
    return out


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


def git_status(repo_path: str) -> dict:
    if not is_repo_available(repo_path):
        return {
            "available": False,
            "reason": (
                f"Repo no accesible desde este proceso (ASKBOX_REPO_PATH={repo_path or '(vacío)'}). "
                "Monta el repo en el contenedor o ejecuta la API en el host para habilitar esta función."
            ),
        }
    branch, _ = _run_git(repo_path, "branch", "--show-current")
    remotes_raw, _ = _run_git(repo_path, "remote", "-v")
    last_commit_raw, _ = _run_git(repo_path, "log", "-1", "--format=%H|%cI|%s")
    upstream, _ = _run_git(repo_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    dirty_raw, _ = _run_git(repo_path, "status", "--porcelain")

    commit = {"hash": None, "date": None, "subject": None}
    if last_commit_raw:
        h, d, s = (last_commit_raw.split("|", 2) + [None, None, None])[:3]
        commit = {"hash": h[:12] if h else None, "date": d, "subject": s}

    return {
        "available": True,
        "branch": branch or None,
        "remotes": parse_remotes(remotes_raw),
        "upstream": upstream,
        "last_commit": commit,
        "dirty": bool(dirty_raw),
        "preferences": load_preferences(repo_path),
    }


def gh_binary() -> str | None:
    return shutil.which("gh")


def set_remote(repo_path: str, name: str, url: str) -> tuple[bool, str | None]:
    existing, _ = _run_git(repo_path, "remote")
    action = "set-url" if (existing and name in existing.split()) else "add"
    _, error = _run_git(repo_path, "remote", action, name, url)
    return error is None, error


def create_remote_with_gh(
    repo_path: str, name: str, visibility: str, slug: str | None = None, push: bool = False,
) -> tuple[bool, str | None]:
    gh = gh_binary()
    if not gh:
        return False, "gh_not_available"
    args = [gh, "repo", "create"]
    if slug:
        args.append(slug)
    args += ["--public" if visibility == "public" else "--private", "--source", repo_path, "--remote", name]
    if push:
        args.append("--push")
    try:
        res = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()[:300]
    return True, None


def mark_pushed(repo_path: str) -> dict:
    return save_preferences(repo_path, {"last_push_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
