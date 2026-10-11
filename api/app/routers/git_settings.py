"""Pantalla de configuración Git del proyecto (MVP, ver .cursor/skills/git-autopilot).

Expone estado de git (remoto saneado, branch, último commit) y preferencias no secretas
guardadas en el propio repo (`.askbox/git-settings.json`). No ejecuta commit/push por sí
mismo: esa decisión la toma el agente/operador con acceso al host. Si el proceso no tiene
acceso al `.git` del repo (p. ej. el contenedor no lo monta), lo informa explícitamente en vez
de simular datos.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import gitops
from app.config import settings
from app.deps import current_user, err

router = APIRouter(prefix="/v1/settings/git", tags=["settings"])

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
    visibility: str = Field(default="private", pattern="^(public|private)$")
    push: bool = False


@router.get("/status")
async def status(user: dict = Depends(current_user)):
    return gitops.git_status(settings.repo_path)


@router.patch("/preferences")
async def update_preferences(body: PreferencesIn, user: dict = Depends(current_user)):
    if not gitops.is_repo_available(settings.repo_path):
        raise err(503, "git_unavailable", "Repo no accesible desde este proceso; no se pueden guardar preferencias.")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise err(422, "empty_body", "Nada que actualizar.")
    return gitops.save_preferences(settings.repo_path, patch)


@router.post("/remote")
async def remote(body: RemoteIn, user: dict = Depends(current_user)):
    repo = settings.repo_path
    if not gitops.is_repo_available(repo):
        raise err(503, "git_unavailable", "Repo no accesible desde este proceso.")
    if body.create:
        ok, error = gitops.create_remote_with_gh(repo, body.name, body.visibility, body.slug, body.push)
        if not ok:
            if error == "gh_not_available":
                raise err(
                    501, "gh_not_available",
                    "gh CLI no disponible/autenticado en el host. Crea el remoto manualmente "
                    "(gh auth login && gh repo create) o usa 'url' para conectar uno existente.",
                )
            raise err(502, "gh_repo_create_failed", f"No se pudo crear el repo remoto: {error}")
    elif body.url:
        ok, error = gitops.set_remote(repo, body.name, body.url)
        if not ok:
            raise err(502, "git_remote_failed", f"No se pudo configurar el remoto: {error}")
    else:
        raise err(
            422, "missing_url_or_create",
            "Indica 'url' para conectar un remoto existente o 'create=true' para crear uno nuevo con gh.",
        )
    return gitops.git_status(repo)
