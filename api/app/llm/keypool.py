"""Pool de claves por proyecto: selección, cooldown y fallos consecutivos."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.crypto import CryptoError, decrypt_secret

log = logging.getLogger(__name__)


@dataclass
class KeyHandle:
    provider: str
    secret: str  # solo en memoria; nunca se loguea ni se serializa
    key_id: str | None  # None = clave de sistema
    source: str  # 'project' | 'system'

    def __repr__(self) -> str:  # evita fugas accidentales en logs
        return f"KeyHandle(provider={self.provider!r}, key_id={self.key_id!r}, source={self.source!r})"


def _system_key(provider: str) -> str:
    return settings.system_gemini_key if provider == "gemini" else settings.system_openrouter_key


async def candidates(pool, project: dict, provider: str) -> list[KeyHandle]:
    rows = await pool.fetch(
        "SELECT id, ciphertext, nonce, key_version FROM api_keys "
        "WHERE project_id = $1 AND provider = $2 AND status = 'active' "
        "AND (cooldown_until IS NULL OR cooldown_until <= now()) "
        "ORDER BY last_used_at NULLS FIRST, created_at",
        project["id"],
        provider,
    )
    out: list[KeyHandle] = []
    for r in rows:
        try:
            secret = decrypt_secret(r["ciphertext"], r["nonce"], r["key_version"], str(project["id"]), provider)
        except CryptoError as exc:
            log.error("clave %s no descifrable: %s", r["id"], exc)
            continue
        out.append(KeyHandle(provider, secret, str(r["id"]), "project"))
    if project["allow_system_keys"] and _system_key(provider):
        out.append(KeyHandle(provider, _system_key(provider), None, "system"))
    return out


async def mark_success(pool, handle: KeyHandle) -> None:
    if handle.key_id:
        await pool.execute(
            "UPDATE api_keys SET consecutive_failures = 0, cooldown_until = NULL, last_used_at = now() WHERE id = $1",
            handle.key_id,
        )


async def mark_quota(pool, handle: KeyHandle, *, message: str = "") -> None:
    if not handle.key_id:
        return
    row = await pool.fetchrow(
        "UPDATE api_keys SET consecutive_failures = consecutive_failures + 1, last_used_at = now(), "
        "cooldown_until = now() + make_interval(secs => LEAST(900, GREATEST(60, power(2, consecutive_failures + 1) * 60))) "
        "WHERE id = $1 RETURNING consecutive_failures",
        handle.key_id,
    )
    failures = int(row["consecutive_failures"]) if row else 0
    msg = (message or "").lower()
    if failures >= 6 or ("exhausted" in msg and failures >= 2):
        await pool.execute("UPDATE api_keys SET status = 'exhausted' WHERE id = $1", handle.key_id)


async def mark_invalid(pool, handle: KeyHandle) -> None:
    if handle.key_id:
        await pool.execute("UPDATE api_keys SET status = 'invalid' WHERE id = $1", handle.key_id)
