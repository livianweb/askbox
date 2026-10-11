"""Pantalla de despliegue: URL pública, túnel Cloudflare (opcional), token cifrado en BD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from app import deploycreds, deployops, gitops
from app.config import settings
from app.crypto import CryptoError
from app.deps import current_user, err

router = APIRouter(prefix="/v1/settings/deploy", tags=["settings"])


class PreferencesIn(BaseModel):
    public_url: str | None = Field(default=None, max_length=500)
    tunnel_enabled: bool | None = None

    @field_validator("public_url")
    @classmethod
    def strip_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return deployops.normalize_public_url(v)


class TunnelTokenIn(BaseModel):
    token: str = Field(max_length=2000)


def _require_repo_writable() -> str:
    path = settings.repo_path
    if not gitops.is_repo_available(path):
        raise err(503, "deploy_unavailable", "Repo no accesible; no se pueden guardar preferencias de despliegue.")
    return path


@router.get("/status")
async def status(request: Request, user: dict = Depends(current_user)):
    path = settings.repo_path
    prefs = deployops.load_preferences(path) if gitops.is_repo_available(path) else dict(deployops.DEFAULT_PREFS)
    effective_url = deployops.effective_public_url(path, settings.public_url) if gitops.is_repo_available(path) else settings.public_url
    cookie_secure = (
        deployops.effective_cookie_secure(path, settings.cookie_secure, settings.public_url)
        if gitops.is_repo_available(path)
        else settings.cookie_secure
    )
    meta = await deploycreds.get_meta(request.app.state.pool, user["id"])
    return {
        "repo_accessible": gitops.is_repo_available(path),
        "preferences": prefs,
        "env_public_url": settings.public_url,
        "env_cookie_secure": settings.cookie_secure,
        "effective_public_url": effective_url,
        "effective_cookie_secure": cookie_secure,
        "suggested_cookie_secure": deployops.public_url_is_https(prefs.get("public_url") or settings.public_url),
        **meta,
        "compose_tunnel_profile": "tunnel",
        "compose_hint": "docker compose --profile tunnel up -d",
        "env_hint": (
            "Copia CLOUDFLARE_TUNNEL_TOKEN desde la UI (guardado cifrado) a .env en el host "
            "y reinicia con el profile tunnel. ASKBOX_PUBLIC_URL y ASKBOX_COOKIE_SECURE en .env "
            "siguen siendo la fuente para el contenedor api hasta que coincidan con lo guardado aquí."
        ),
    }


@router.patch("/preferences")
async def update_preferences(body: PreferencesIn, user: dict = Depends(current_user)):
    path = _require_repo_writable()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise err(422, "empty_body", "Nada que actualizar.")
    prefs = deployops.save_preferences(path, patch)
    effective_url = deployops.effective_public_url(path, settings.public_url)
    cookie_secure = deployops.effective_cookie_secure(path, settings.cookie_secure, settings.public_url)
    return {
        "preferences": prefs,
        "effective_public_url": effective_url,
        "effective_cookie_secure": cookie_secure,
        "suggested_env": {
            "ASKBOX_PUBLIC_URL": effective_url,
            "ASKBOX_COOKIE_SECURE": "true" if deployops.public_url_is_https(effective_url) else "false",
        },
        "note": "Reinicia el contenedor api (o actualiza .env) para alinear cookies y enlaces con el entorno Docker.",
    }


@router.put("/tunnel-token")
@router.post("/tunnel-token")
async def save_tunnel_token(body: TunnelTokenIn, request: Request, user: dict = Depends(current_user)):
    if not deploycreds.master_key_ready():
        raise err(503, "master_key_missing", "ASKBOX_MASTER_KEY requerida para cifrar el token.")
    token = body.token.strip()
    if not deploycreds.valid_token_format(token):
        raise err(422, "invalid_token", "Formato de token no válido.")
    try:
        meta = await deploycreds.save_token(request.app.state.pool, user["id"], token)
    except CryptoError:
        raise err(503, "master_key_invalid", "No se pudo cifrar el token (ASKBOX_MASTER_KEY inválida).") from None
    return {
        **meta,
        "note": "Pega el mismo valor en CLOUDFLARE_TUNNEL_TOKEN en .env del host y ejecuta: docker compose --profile tunnel up -d",
    }


@router.delete("/tunnel-token")
async def delete_tunnel_token(request: Request, user: dict = Depends(current_user)):
    deleted = await deploycreds.delete_token(request.app.state.pool, user["id"])
    return {"tunnel_token_configured": False, "deleted": deleted}
