"""Pantalla de configuración Git del proyecto (ver .cursor/skills/git-autopilot).

Expone estado de git (remoto saneado, branch, último commit), preferencias no secretas
guardadas en el propio repo (`.askbox/git-settings.json`) y la gestión del **GitHub token**:
cifrado con AES-256-GCM en BD (`git_credentials`, **un token por repo**), jamás devuelto por la API (solo
`github_token_configured` y `last4`). Con token (o `gh` autenticado) se puede crear el repo
remoto y hacer push. Si el proceso no tiene acceso al `.git` del repo (p. ej. el contenedor no
lo monta), lo informa explícitamente en vez de simular datos.

`?repo=<nombre>` selecciona un repo adicional montado (`ASKBOX_EXTRA_REPOS`); por defecto, el
propio repo de askbox.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app import gitcreds, gitops
from app.config import settings
from app.crypto import CryptoError
from app.deps import current_user, err

router = APIRouter(prefix="/v1/settings/git", tags=["settings"])

PRIMARY_REPO = "askbox"
_NAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class PreferencesIn(BaseModel):
    auto_commit: bool | None = None
    remote_name: str | None = Field(default=None, min_length=1, max_length=50, pattern=_NAME_PATTERN)
    default_visibility: str | None = Field(default=None, pattern="^(public|private)$")


class RemoteIn(BaseModel):
    name: str = Field(default="origin", min_length=1, max_length=50, pattern=_NAME_PATTERN)
    url: str | None = Field(default=None, max_length=500)
    create: bool = False
    slug: str | None = Field(default=None, max_length=100, pattern=_NAME_PATTERN)
    visibility: str | None = Field(default=None, pattern="^(public|private)$")  # None -> preferencia del repo
    description: str | None = Field(default=None, max_length=300)
    push: bool = False


class PushIn(BaseModel):
    remote: str | None = Field(default=None, min_length=1, max_length=50, pattern=_NAME_PATTERN)
    branch: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_./-]+$")


class TokenIn(BaseModel):
    token: str = Field(max_length=500)


def _resolve_repo(name: str | None) -> tuple[str, str]:
    """(nombre, ruta). Solo el repo propio o los declarados en ASKBOX_EXTRA_REPOS."""
    name = name or PRIMARY_REPO
    if name == PRIMARY_REPO:
        return name, settings.repo_path
    path = settings.extra_repos.get(name)
    if not path:
        raise err(404, "repo_not_found", f"Repo '{name}' no configurado (ver ASKBOX_EXTRA_REPOS).")
    return name, path


def _repo_names() -> list[str]:
    return [PRIMARY_REPO, *settings.extra_repos.keys()]


def _require_repo(name: str | None) -> tuple[str, str]:
    name, path = _resolve_repo(name)
    if not gitops.is_repo_available(path):
        raise err(503, "git_unavailable", "Repo no accesible desde este proceso.")
    return name, path


async def _token_meta(request: Request, user: dict, repo: str) -> dict:
    """Metadatos del token **de ese repo** (nunca el token)."""
    return {**await gitcreds.get_meta(request.app.state.pool, user["id"], repo), "github_token_repo": repo}


async def _status_payload(request: Request, user: dict, name: str, path: str) -> dict:
    out = await asyncio.to_thread(gitops.git_status, path)
    out["repo"] = name
    out["repos"] = _repo_names()
    out.update(await _token_meta(request, user, name))
    out["gh_authenticated"] = await asyncio.to_thread(gitops.gh_authenticated)
    return out


async def _load_token(request: Request, user: dict, repo: str) -> str | None:
    try:
        return await gitcreds.load_token(request.app.state.pool, user["id"], repo)
    except CryptoError:
        raise err(503, "master_key_invalid", "No se pudo descifrar el GitHub token (ASKBOX_MASTER_KEY ausente o distinta).") from None


@router.get("/status")
async def status(request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user)):
    name, path = _resolve_repo(repo)
    return await _status_payload(request, user, name, path)


@router.patch("/preferences")
async def update_preferences(body: PreferencesIn, user: dict = Depends(current_user)):
    # Solo el repo propio: los repos adicionales se montan en solo lectura (salvo .git).
    if not gitops.is_repo_available(settings.repo_path):
        raise err(503, "git_unavailable", "Repo no accesible desde este proceso; no se pueden guardar preferencias.")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise err(422, "empty_body", "Nada que actualizar.")
    return gitops.save_preferences(settings.repo_path, patch)


# --- GitHub token (cifrado en BD; nunca se devuelve) ------------------------------------------

@router.get("/github-token")
async def github_token_status(
    request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user),
):
    name, _ = _resolve_repo(repo)
    return await _token_meta(request, user, name)


@router.put("/github-token")
@router.post("/github-token")
async def github_token_save(
    body: TokenIn, request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user),
):
    name, _ = _resolve_repo(repo)
    token = body.token.strip()
    if not gitcreds.valid_token_format(token):
        raise err(422, "invalid_token", "Formato de token no válido (20-255 caracteres ASCII sin espacios).")
    if not gitcreds.master_key_ready():
        raise err(503, "master_key_missing", "ASKBOX_MASTER_KEY no está configurada en el servidor.")
    try:
        await gitcreds.save_token(request.app.state.pool, user["id"], name, token)
    except CryptoError:
        raise err(503, "master_key_invalid", "ASKBOX_MASTER_KEY no es válida.") from None
    return await _token_meta(request, user, name)


@router.delete("/github-token")
async def github_token_delete(
    request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user),
):
    name, _ = _resolve_repo(repo)
    await gitcreds.delete_token(request.app.state.pool, user["id"], name)
    return {"github_token_configured": False, "github_token_repo": name}


# --- Remoto y push ------------------------------------------------------------------------------

@router.post("/remote")
async def remote(
    body: RemoteIn, request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user),
):
    name, path = _require_repo(repo)
    created = None
    if body.create:
        prefs = gitops.load_preferences(path)
        visibility = body.visibility or prefs.get("default_visibility") or "private"
        if await asyncio.to_thread(gitops.gh_authenticated):
            ok, error = await asyncio.to_thread(
                gitops.create_remote_with_gh, path, body.name, visibility, body.slug, body.push,
            )
            if not ok:
                raise err(502, "gh_repo_create_failed", f"No se pudo crear el repo remoto: {error}")
        else:
            token = await _load_token(request, user, name)
            if not token:
                raise err(
                    501, "gh_not_available",
                    f"Sin credenciales de GitHub: guarda el GitHub token del repo '{name}' en /settings/git "
                    f"(PUT /v1/settings/git/github-token?repo={name}), o haz 'gh auth login' en el host, "
                    "o usa 'url' para conectar un remoto existente.",
                )
            created = await _create_with_token(path, body, visibility, token, push_prefs=(name == PRIMARY_REPO))
    elif body.url:
        ok, error = await asyncio.to_thread(gitops.set_remote, path, body.name, body.url)
        if not ok:
            raise err(502, "git_remote_failed", f"No se pudo configurar el remoto: {gitops.redact(error)}")
    else:
        raise err(
            422, "missing_url_or_create",
            "Indica 'url' para conectar un remoto existente o 'create=true' para crear uno nuevo.",
        )
    out = await _status_payload(request, user, name, path)
    if created:
        out["created_repo"] = created
    return out


async def _create_with_token(path: str, body: RemoteIn, visibility: str, token: str, push_prefs: bool) -> dict:
    """Crea el repo con la REST API de GitHub, conecta el remoto (URL limpia) y opcionalmente pushea."""
    if not body.slug:
        raise err(422, "slug_required", "Indica el nombre del repo (slug) para crearlo con el token.")
    existing, _ = await asyncio.to_thread(gitops._run_git, path, "remote")
    if existing and body.name in existing.split():
        if body.push:
            ok, error = await asyncio.to_thread(gitops.push_branch, path, body.name, None, token)
            if not ok:
                raise err(502, "github_push_failed", f"El remoto ya existía pero el push falló: {error}")
            if push_prefs:
                await asyncio.to_thread(gitops.mark_pushed, path)
            url, _ = await asyncio.to_thread(gitops._run_git, path, "remote", "get-url", body.name)
            clean = gitops.sanitize_remote_url(url or "")
            return {
                "full_name": None,
                "html_url": clean.removesuffix(".git") if clean else None,
                "clone_url": clean or url,
                "private": None,
                "connected_existing_remote": True,
            }
        raise err(409, "remote_exists", f"El remoto '{body.name}' ya existe; usa otro nombre o conéctalo con 'url'.")
    info, code, message = await asyncio.to_thread(
        gitops.github_create_repo, token, body.slug, visibility == "private", body.description,
    )
    if not info and code == "github_repo_exists":
        login, lcode, lmsg = await asyncio.to_thread(gitops.github_authenticated_login, token)
        if not login:
            status_code = 401 if lcode == "github_token_invalid" else 502
            raise err(status_code, lcode or "github_error", lmsg or "No se pudo leer el usuario de GitHub.")
        info, code, message = await asyncio.to_thread(gitops.github_get_repo, token, login, body.slug)
    if not info:
        status_code = 409 if code == "github_repo_exists" else (401 if code == "github_token_invalid" else 502)
        raise err(status_code, code or "github_error", message or "Error de GitHub.")
    ok, error = await asyncio.to_thread(gitops.set_remote, path, body.name, info["clone_url"])
    if not ok:
        raise err(502, "git_remote_failed", f"Repo creado ({info['html_url']}) pero no se pudo conectar el remoto: {gitops.redact(error, token)}")
    if body.push:
        ok, error = await asyncio.to_thread(gitops.push_branch, path, body.name, None, token)
        if not ok:
            raise err(502, "github_push_failed", f"Repo creado ({info['html_url']}) y remoto conectado, pero el push falló: {error}")
        if push_prefs:
            await asyncio.to_thread(gitops.mark_pushed, path)
    return info


@router.post("/push")
async def push(
    body: PushIn, request: Request, repo: str | None = Query(default=None), user: dict = Depends(current_user),
):
    """Push de la rama actual (o `branch`) al remoto. Usa el GitHub token guardado **para ese repo** si lo hay."""
    name, path = _require_repo(repo)
    prefs = gitops.load_preferences(path)
    remote_name = body.remote or prefs.get("remote_name") or "origin"
    token = await _load_token(request, user, name)
    ok, error = await asyncio.to_thread(gitops.push_branch, path, remote_name, body.branch, token)
    if not ok:
        raise err(502, "git_push_failed", f"No se pudo pushear: {error}")
    if name == PRIMARY_REPO:
        await asyncio.to_thread(gitops.mark_pushed, path)
    return await _status_payload(request, user, name, path)
