"""Operaciones git para la pantalla de configuración Git.

Este módulo **no** hace commit: esa decisión la toma el agente/operador con acceso al host
siguiendo `.cursor/skills/git-autopilot`. Expone estado del repo, preferencias no secretas y,
si hay un GitHub token guardado (cifrado en BD, ver `gitcreds.py`), crear el repo remoto vía
REST y hacer push. El token solo vive en memoria durante la operación: se pasa a git por
variables de entorno del subproceso (nunca en argv, nunca en `.git/config`) y toda salida de
error se redacta antes de salir (ver `no-secret-leak.mdc`: nunca URLs con credenciales
embebidas, nunca contenido de archivos del repo salvo el propio archivo de preferencias).
"""
from __future__ import annotations

import base64
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
    "default_visibility": "public",
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


# --- GitHub vía token guardado (REST + push con cabecera en entorno del subproceso) ---------

GITHUB_API = "https://api.github.com"
_GITHUB_HTTPS_PREFIX = "https://github.com/"


def redact(text: str | None, *secrets: str) -> str:
    """Quita de un mensaje el token y sus derivados (base64 de la cabecera Basic)."""
    out = text or ""
    for s in secrets:
        if not s:
            continue
        for variant in (s, base64.b64encode(f"x-access-token:{s}".encode()).decode()):
            out = out.replace(variant, "***")
    return _URL_CREDS.sub("://", out)


def gh_authenticated() -> bool:
    gh = gh_binary()
    if not gh:
        return False
    try:
        res = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _git_env(token: str | None) -> dict:
    """Entorno para git. Con token: cabecera Basic solo hacia github.com, sin credential helpers."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env
    n = int(env.get("GIT_CONFIG_COUNT") or 0)  # conserva safe.directory etc. ya definidos
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env[f"GIT_CONFIG_KEY_{n}"] = f"http.{_GITHUB_HTTPS_PREFIX}.extraheader"
    env[f"GIT_CONFIG_VALUE_{n}"] = f"AUTHORIZATION: basic {basic}"
    env[f"GIT_CONFIG_KEY_{n + 1}"] = "credential.helper"
    env[f"GIT_CONFIG_VALUE_{n + 1}"] = ""
    env["GIT_CONFIG_COUNT"] = str(n + 2)
    return env


def has_commits(repo_path: str) -> bool:
    out, _ = _run_git(repo_path, "rev-parse", "--verify", "HEAD")
    return bool(out)


def push_branch(
    repo_path: str, remote: str, branch: str | None = None, token: str | None = None,
) -> tuple[bool, str | None]:
    """`git push -u <remote> <branch>`. El `token` solo se aplica a remotos https://github.com/..."""
    branch = branch or _run_git(repo_path, "branch", "--show-current")[0]
    if not branch:
        return False, "No hay rama actual (HEAD desacoplado)."
    if not has_commits(repo_path):
        return False, "El repo no tiene commits todavía; haz commit antes de pushear."
    url, _ = _run_git(repo_path, "remote", "get-url", remote)
    if not url:
        return False, f"El remoto '{remote}' no existe."
    if token and not url.startswith(_GITHUB_HTTPS_PREFIX):
        token = None  # el token solo se usa contra https://github.com/...; otros remotos usan las credenciales del host
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "push", "-u", remote, branch],
            capture_output=True, text=True, timeout=120, check=False, env=_git_env(token),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, redact(str(exc), token or "")
    if res.returncode != 0:
        msg = redact((res.stderr or res.stdout).strip(), token or "")[:300]
        if "could not read Username" in msg:
            msg = (
                "GitHub no aceptó las credenciales o el repo no existe/no tienes permiso "
                "(guarda un GitHub token válido con acceso de escritura al repo)."
            )
        return False, msg
    return True, None


def github_create_repo(
    token: str, name: str, private: bool, description: str | None = None,
) -> tuple[dict | None, str | None, str | None]:
    """POST /user/repos. Devuelve (info, error_code, mensaje); info solo con metadatos públicos."""
    import httpx  # import local: solo se usa en esta operación

    try:
        res = httpx.post(
            f"{GITHUB_API}/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "askbox",
            },
            json={"name": name, "private": private, "description": description or "", "auto_init": False},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return None, "github_unreachable", f"No se pudo contactar con GitHub ({type(exc).__name__})."
    if res.status_code == 201:
        data = res.json()
        return {
            "full_name": data.get("full_name"),
            "html_url": data.get("html_url"),
            "clone_url": data.get("clone_url"),
            "private": bool(data.get("private")),
        }, None, None
    try:
        gh_msg = str(res.json().get("message", ""))[:200]
    except ValueError:
        gh_msg = ""
    if res.status_code == 401:
        return None, "github_token_invalid", "GitHub rechazó el token (inválido, revocado o caducado)."
    if res.status_code == 403:
        if "resource not accessible by personal access token" in gh_msg.lower():
            return (
                None,
                "github_forbidden",
                "El PAT fine-grained no puede crear repos: en GitHub → Settings → Developer settings → "
                "Fine-grained tokens, añade permiso de cuenta «Administration: Read and write» "
                "(o usa un PAT clásico con alcance «repo»).",
            )
        return None, "github_forbidden", "El token no tiene permisos para crear repos (necesita 'repo' / Administration: write)."
    if res.status_code == 422:
        return None, "github_repo_exists", f"GitHub no pudo crear el repo (¿ya existe ese nombre?): {redact(gh_msg, token)}"
    return None, "github_error", f"GitHub respondió {res.status_code}: {redact(gh_msg, token)}"


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "askbox",
    }


def github_authenticated_login(token: str) -> tuple[str | None, str | None, str | None]:
    """GET /user → login. Devuelve (login, error_code, mensaje)."""
    import httpx

    try:
        res = httpx.get(f"{GITHUB_API}/user", headers=_github_headers(token), timeout=20.0)
    except httpx.HTTPError as exc:
        return None, "github_unreachable", f"No se pudo contactar con GitHub ({type(exc).__name__})."
    if res.status_code == 200:
        login = (res.json().get("login") or "").strip()
        return (login or None), None, None
    if res.status_code == 401:
        return None, "github_token_invalid", "GitHub rechazó el token (inválido, revocado o caducado)."
    return None, "github_error", f"GitHub respondió {res.status_code} al leer el usuario."


def github_get_repo(token: str, owner: str, name: str) -> tuple[dict | None, str | None, str | None]:
    """GET /repos/{owner}/{name}. Metadatos públicos como github_create_repo."""
    import httpx

    try:
        res = httpx.get(
            f"{GITHUB_API}/repos/{owner}/{name}", headers=_github_headers(token), timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return None, "github_unreachable", f"No se pudo contactar con GitHub ({type(exc).__name__})."
    if res.status_code == 200:
        data = res.json()
        return {
            "full_name": data.get("full_name"),
            "html_url": data.get("html_url"),
            "clone_url": data.get("clone_url"),
            "private": bool(data.get("private")),
        }, None, None
    if res.status_code == 404:
        return None, "github_repo_not_found", f"No se encontró el repo '{owner}/{name}' en GitHub."
    if res.status_code == 401:
        return None, "github_token_invalid", "GitHub rechazó el token (inválido, revocado o caducado)."
    return None, "github_error", f"GitHub respondió {res.status_code} al leer el repo."


def github_patch_repo(
    token: str, owner: str, name: str, private: bool,
) -> tuple[dict | None, str | None, str | None]:
    """PATCH /repos/{owner}/{name} — p. ej. visibilidad public/private."""
    import httpx

    try:
        res = httpx.patch(
            f"{GITHUB_API}/repos/{owner}/{name}",
            headers=_github_headers(token),
            json={"private": private},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return None, "github_unreachable", f"No se pudo contactar con GitHub ({type(exc).__name__})."
    if res.status_code == 200:
        data = res.json()
        return {
            "full_name": data.get("full_name"),
            "html_url": data.get("html_url"),
            "clone_url": data.get("clone_url"),
            "private": bool(data.get("private")),
        }, None, None
    try:
        gh_msg = str(res.json().get("message", ""))[:200]
    except ValueError:
        gh_msg = ""
    if res.status_code == 401:
        return None, "github_token_invalid", "GitHub rechazó el token (inválido, revocado o caducado)."
    if res.status_code == 403:
        return None, "github_forbidden", f"El token no puede modificar el repo: {redact(gh_msg, token)}"
    if res.status_code == 404:
        return None, "github_repo_not_found", f"No se encontró el repo '{owner}/{name}' en GitHub."
    return None, "github_error", f"GitHub respondió {res.status_code}: {redact(gh_msg, token)}"
