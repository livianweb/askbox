"""Provisionar Cloudflare Tunnel + DNS vía API (sin cloudflared CLI)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

_CF_API = "https://api.cloudflare.com/client/v4"
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


class CloudflareApiError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 502):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass
class ProvisionResult:
    account_id: str
    tunnel_id: str
    tunnel_name: str
    hostname: str
    connector_token: str
    dns_record_id: str | None
    reused_tunnel: bool


def normalize_hostname(raw: str) -> str:
    host = (raw or "").strip().lower().rstrip(".")
    if host.startswith("https://") or host.startswith("http://"):
        host = urlparse(host).hostname or ""
    if not host or not _HOSTNAME_RE.match(host):
        raise CloudflareApiError("invalid_hostname", "Hostname no válido (ej. askbox.ejemplo.com).", 422)
    return host


def zone_name_candidates(hostname: str) -> list[str]:
    labels = hostname.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def _headers(api_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}


def _api_errors(data: dict) -> tuple[int, str]:
    errors = data.get("errors") or []
    if not errors:
        return 0, "Error desconocido de Cloudflare"
    first = errors[0]
    return int(first.get("code") or 0), str(first.get("message") or "Error de Cloudflare")


async def _request(client: httpx.AsyncClient, method: str, path: str, api_token: str, **kwargs) -> dict:
    try:
        res = await client.request(method, f"{_CF_API}{path}", headers=_headers(api_token), **kwargs)
    except httpx.HTTPError as exc:
        raise CloudflareApiError("cf_unreachable", "No se pudo contactar con la API de Cloudflare.", 502) from exc
    try:
        data = res.json()
    except ValueError:
        raise CloudflareApiError("cf_bad_response", "Respuesta inválida de Cloudflare.", 502) from None
    if not data.get("success"):
        cf_code, msg = _api_errors(data)
        code = "cf_forbidden" if res.status_code in {401, 403} or cf_code in {10000, 9109} else "cf_api_error"
        if "tunnel" in msg.lower() and "permission" in msg.lower():
            code = "cf_tunnel_permission"
        if "dns" in msg.lower() or "zone" in msg.lower():
            code = "cf_dns_permission"
        raise CloudflareApiError(code, msg, 502 if res.status_code >= 500 else 422)
    return data


async def verify_api_token(api_token: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _request(client, "GET", "/user/tokens/verify", api_token)
        status = (data.get("result") or {}).get("status")
        if status != "active":
            raise CloudflareApiError("cf_token_inactive", "El token de API no está activo.", 422)
        accounts = await _request(client, "GET", "/accounts?per_page=1", api_token)
        results = accounts.get("result") or []
        if not results:
            raise CloudflareApiError("cf_no_account", "No se encontró ninguna cuenta Cloudflare para este token.", 422)
        return results[0]["id"]


async def _find_tunnel(client: httpx.AsyncClient, account_id: str, api_token: str, name: str) -> dict | None:
    data = await _request(client, "GET", f"/accounts/{account_id}/cfd_tunnel", api_token, params={"name": name, "is_deleted": "false"})
    for row in data.get("result") or []:
        if row.get("name") == name and not row.get("deleted_at"):
            return row
    return None


async def _create_tunnel(client: httpx.AsyncClient, account_id: str, api_token: str, name: str) -> dict:
    data = await _request(
        client,
        "POST",
        f"/accounts/{account_id}/cfd_tunnel",
        api_token,
        json={"name": name, "config_src": "cloudflare"},
    )
    return data["result"]


async def _put_ingress(
    client: httpx.AsyncClient,
    account_id: str,
    api_token: str,
    tunnel_id: str,
    hostname: str,
) -> None:
    body = {
        "config": {
            "ingress": [
                {"hostname": hostname, "service": "http://web:3000", "originRequest": {}},
                {"service": "http_status:404"},
            ],
        },
    }
    await _request(client, "PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations", api_token, json=body)


async def _connector_token(client: httpx.AsyncClient, account_id: str, api_token: str, tunnel_id: str) -> str:
    data = await _request(client, "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token", api_token)
    token = data.get("result")
    if not token or not isinstance(token, str):
        raise CloudflareApiError("cf_no_connector_token", "Cloudflare no devolvió el token del connector.", 502)
    return token


async def _resolve_zone(client: httpx.AsyncClient, api_token: str, hostname: str) -> tuple[str, str]:
    for candidate in zone_name_candidates(hostname):
        data = await _request(client, "GET", "/zones", api_token, params={"name": candidate, "status": "active"})
        zones = data.get("result") or []
        if zones:
            zone = zones[0]
            zone_name = zone["name"]
            if hostname == zone_name:
                record_name = zone_name
            elif hostname.endswith("." + zone_name):
                record_name = hostname[: -(len(zone_name) + 1)]
            else:
                record_name = hostname
            return zone["id"], record_name
    raise CloudflareApiError(
        "cf_zone_not_found",
        "No se encontró zona DNS activa para este hostname. El token necesita Zone DNS Edit en la zona correcta.",
        422,
    )


async def _upsert_dns_cname(
    client: httpx.AsyncClient,
    api_token: str,
    zone_id: str,
    record_name: str,
    tunnel_id: str,
) -> str:
    content = f"{tunnel_id}.cfargotunnel.com"
    list_data = await _request(
        client,
        "GET",
        f"/zones/{zone_id}/dns_records",
        api_token,
        params={"type": "CNAME", "name": record_name},
    )
    existing = list_data.get("result") or []
    payload = {"type": "CNAME", "name": record_name, "content": content, "proxied": True, "ttl": 1}
    if existing:
        rec_id = existing[0]["id"]
        await _request(client, "PUT", f"/zones/{zone_id}/dns_records/{rec_id}", api_token, json=payload)
        return rec_id
    created = await _request(client, "POST", f"/zones/{zone_id}/dns_records", api_token, json=payload)
    return created["result"]["id"]


async def provision_tunnel(api_token: str, hostname: str, tunnel_name: str = "askbox") -> ProvisionResult:
    hostname = normalize_hostname(hostname)
    tunnel_name = (tunnel_name or "askbox").strip()[:63] or "askbox"
    async with httpx.AsyncClient(timeout=45.0) as client:
        account_id = await verify_api_token(api_token)
        existing = await _find_tunnel(client, account_id, api_token, tunnel_name)
        reused = existing is not None
        tunnel = existing or await _create_tunnel(client, account_id, api_token, tunnel_name)
        tunnel_id = tunnel["id"]
        await _put_ingress(client, account_id, api_token, tunnel_id, hostname)
        connector = await _connector_token(client, account_id, api_token, tunnel_id)
        zone_id, record_name = await _resolve_zone(client, api_token, hostname)
        dns_id = await _upsert_dns_cname(client, api_token, zone_id, record_name, tunnel_id)
    return ProvisionResult(
        account_id=account_id,
        tunnel_id=tunnel_id,
        tunnel_name=tunnel_name,
        hostname=hostname,
        connector_token=connector,
        dns_record_id=dns_id,
        reused_tunnel=reused,
    )


def default_tunnel_name(repo_path: str) -> str:
    import os

    base = os.path.basename(os.path.abspath(repo_path.rstrip("/"))) or "askbox"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", base)[:63]
    return safe or "askbox"
