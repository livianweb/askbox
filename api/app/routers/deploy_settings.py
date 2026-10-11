"""Pantalla de despliegue: URL pública, túnel Cloudflare (opcional), tokens cifrados en BD."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from app import cfcreds, cf_tunnel, deploycreds, deployops, gitops, tunnel_apply
from app.cf_tunnel import CloudflareApiError
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


class CloudflareApiTokenIn(BaseModel):
    token: str = Field(max_length=512)


class TunnelProvisionIn(BaseModel):
    hostname: str = Field(min_length=4, max_length=253)
    api_token: str | None = Field(default=None, max_length=512)
    save_api_token: bool = True


def _require_repo_writable() -> str:
    path = settings.repo_path
    if not gitops.is_repo_available(path):
        raise err(503, "deploy_unavailable", "Repo no accesible; no se pueden guardar preferencias de despliegue.")
    return path


def _tunnel_status_block(path: str) -> dict:
    prefs = deployops.load_preferences(path) if gitops.is_repo_available(path) else {}
    return {
        "tunnel_id": prefs.get("tunnel_id") or "",
        "tunnel_hostname": prefs.get("tunnel_hostname") or "",
        "tunnel_name": prefs.get("tunnel_name") or "askbox",
        "tunnel_provisioned_at": prefs.get("tunnel_provisioned_at"),
        "cloudflared_running": tunnel_apply.cloudflared_running(),
    }


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
    tunnel_meta = await deploycreds.get_meta(request.app.state.pool, user["id"])
    cf_meta = await cfcreds.get_meta(request.app.state.pool, user["id"])
    return {
        "repo_accessible": gitops.is_repo_available(path),
        "preferences": prefs,
        "env_public_url": settings.public_url,
        "env_cookie_secure": settings.cookie_secure,
        "effective_public_url": effective_url,
        "effective_cookie_secure": cookie_secure,
        "suggested_cookie_secure": deployops.public_url_is_https(prefs.get("public_url") or settings.public_url),
        **tunnel_meta,
        **cf_meta,
        **_tunnel_status_block(path),
        "compose_tunnel_profile": "tunnel",
        "compose_hint": "docker compose --profile tunnel up -d cloudflared",
        "provision_hint": (
            "Pega un token de API Cloudflare (Account: Cloudflare Tunnel Edit + Zone DNS Edit), "
            "el hostname deseado y pulsa «Crear / actualizar túnel»."
        ),
        "env_hint": (
            "Con ASKBOX_HOST_ENV_FILE montado o scripts/apply-tunnel.sh en el host, la API puede "
            "actualizar CLOUDFLARE_TUNNEL_TOKEN y levantar cloudflared tras el provision."
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


@router.put("/cloudflare-api-token")
@router.post("/cloudflare-api-token")
async def save_cloudflare_api_token(body: CloudflareApiTokenIn, request: Request, user: dict = Depends(current_user)):
    if not cfcreds.master_key_ready():
        raise err(503, "master_key_missing", "ASKBOX_MASTER_KEY requerida para cifrar el token.")
    token = body.token.strip()
    if not cfcreds.valid_token_format(token):
        raise err(422, "invalid_token", "Formato de token de API Cloudflare no válido.")
    try:
        meta = await cfcreds.save_token(request.app.state.pool, user["id"], token)
    except CryptoError:
        raise err(503, "master_key_invalid", "No se pudo cifrar el token (ASKBOX_MASTER_KEY inválida).") from None
    return {**meta, "note": "Token de API guardado (cifrado). Solo se usa al provisionar el túnel."}


@router.delete("/cloudflare-api-token")
async def delete_cloudflare_api_token(request: Request, user: dict = Depends(current_user)):
    deleted = await cfcreds.delete_token(request.app.state.pool, user["id"])
    return {"cloudflare_api_token_configured": False, "deleted": deleted}


@router.post("/tunnel/provision")
async def provision_tunnel(body: TunnelProvisionIn, request: Request, user: dict = Depends(current_user)):
    if not deploycreds.master_key_ready():
        raise err(503, "master_key_missing", "ASKBOX_MASTER_KEY requerida para cifrar credenciales.")
    path = _require_repo_writable()
    api_token = (body.api_token or "").strip()
    if not api_token:
        try:
            api_token = await cfcreds.load_token(request.app.state.pool, user["id"]) or ""
        except CryptoError:
            raise err(503, "master_key_invalid", "No se pudo descifrar el token de API guardado.") from None
    if not api_token:
        raise err(422, "cf_api_token_missing", "Indica un token de API Cloudflare o guárdalo antes en esta pantalla.")
    if body.save_api_token and body.api_token:
        if not cfcreds.valid_token_format(api_token):
            raise err(422, "invalid_token", "Formato de token de API Cloudflare no válido.")
        try:
            await cfcreds.save_token(request.app.state.pool, user["id"], api_token)
        except CryptoError:
            raise err(503, "master_key_invalid", "No se pudo cifrar el token de API.") from None

    tunnel_name = cf_tunnel.default_tunnel_name(path)
    try:
        hostname = cf_tunnel.normalize_hostname(body.hostname)
        result = await cf_tunnel.provision_tunnel(api_token, hostname, tunnel_name=tunnel_name)
    except CloudflareApiError as exc:
        raise err(exc.http_status, exc.code, exc.message) from None

    public_url = f"https://{result.hostname}"
    try:
        await deploycreds.save_token(request.app.state.pool, user["id"], result.connector_token)
    except CryptoError:
        raise err(503, "master_key_invalid", "Túnel creado en Cloudflare pero no se pudo guardar el connector token.") from None

    now = datetime.now(timezone.utc).isoformat()
    prefs = deployops.save_preferences(
        path,
        {
            "public_url": public_url,
            "tunnel_enabled": True,
            "tunnel_id": result.tunnel_id,
            "tunnel_hostname": result.hostname,
            "tunnel_name": result.tunnel_name,
            "tunnel_provisioned_at": now,
        },
    )
    apply_info = tunnel_apply.apply_local(path, result.connector_token, public_url)

    return {
        "ok": True,
        "tunnel_id": result.tunnel_id,
        "tunnel_hostname": result.hostname,
        "tunnel_name": result.tunnel_name,
        "reused_tunnel": result.reused_tunnel,
        "public_url": public_url,
        "preferences": prefs,
        "tunnel_token_configured": True,
        "effective_public_url": public_url,
        "effective_cookie_secure": True,
        **apply_info,
        "note": (
            "Túnel y DNS configurados en Cloudflare. "
            + apply_info.get("docker_note", "")
            + " "
            + apply_info.get("env_note", "")
        ).strip(),
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
        "note": "Token del connector guardado. Usa «Crear / actualizar túnel» o scripts/apply-tunnel.sh para alinear .env y cloudflared.",
    }


@router.delete("/tunnel-token")
async def delete_tunnel_token(request: Request, user: dict = Depends(current_user)):
    deleted = await deploycreds.delete_token(request.app.state.pool, user["id"])
    return {"tunnel_token_configured": False, "deleted": deleted}
