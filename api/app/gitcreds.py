"""Token de GitHub cifrado en BD (tabla `git_credentials`, AES-256-GCM como `api_keys`).

Un token **por usuario y por repositorio** (`repo` = `askbox` o un nombre de ASKBOX_EXTRA_REPOS).
El token entra una vez, se cifra y solo se descifra en memoria para una operación concreta
(crear repo / push). Nunca se loguea ni se devuelve por la API: solo `configured` y `last4`.
"""
from __future__ import annotations

from app.crypto import CryptoError, decrypt_secret, encrypt_secret, master_key_ready

PROVIDER = "github"
PRIMARY_REPO = "askbox"


def _aad_scope(user_id, repo: str) -> str:
    # El repo principal conserva el AAD histórico (migración 004 no re-cifra el token global antiguo).
    return f"user:{user_id}" if repo == PRIMARY_REPO else f"user:{user_id}:repo:{repo}"


def valid_token_format(token: str) -> bool:
    return 20 <= len(token) <= 255 and token.isascii() and token.isprintable() and not any(c.isspace() for c in token)


async def get_meta(pool, user_id, repo: str) -> dict:
    row = await pool.fetchrow(
        "SELECT last4, updated_at FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3",
        user_id, PROVIDER, repo,
    )
    if not row:
        return {"github_token_configured": False}
    return {"github_token_configured": True, "github_token_last4": row["last4"], "github_token_updated_at": row["updated_at"]}


async def save_token(pool, user_id, repo: str, token: str) -> dict:
    """Cifra y hace upsert. Lanza CryptoError si la master key falta o es inválida."""
    ct, nonce, version = encrypt_secret(token, _aad_scope(user_id, repo), PROVIDER)
    await pool.execute(
        "INSERT INTO git_credentials(user_id, provider, repo, ciphertext, nonce, key_version, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (user_id, provider, repo) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
        "key_version = EXCLUDED.key_version, last4 = EXCLUDED.last4, updated_at = now()",
        user_id, PROVIDER, repo, ct, nonce, version, token[-4:],
    )
    return await get_meta(pool, user_id, repo)


async def delete_token(pool, user_id, repo: str) -> bool:
    res = await pool.execute(
        "DELETE FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3", user_id, PROVIDER, repo,
    )
    return not res.endswith(" 0")


async def load_token(pool, user_id, repo: str) -> str | None:
    """Descifra el token del repo en memoria. None si no hay. Lanza CryptoError si no se puede descifrar."""
    row = await pool.fetchrow(
        "SELECT ciphertext, nonce, key_version FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3",
        user_id, PROVIDER, repo,
    )
    if not row:
        return None
    return decrypt_secret(row["ciphertext"], row["nonce"], row["key_version"], _aad_scope(user_id, repo), PROVIDER)


__all__ = ["CryptoError", "master_key_ready", "valid_token_format", "get_meta", "save_token", "delete_token", "load_token"]
