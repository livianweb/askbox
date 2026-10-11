"""Token de GitHub cifrado en BD (tabla `git_credentials`, AES-256-GCM como `api_keys`).

El token entra una vez, se cifra y solo se descifra en memoria para una operación concreta
(crear repo / push). Nunca se loguea ni se devuelve por la API: solo `configured` y `last4`.
"""
from __future__ import annotations

from app.crypto import CryptoError, decrypt_secret, encrypt_secret, master_key_ready

PROVIDER = "github"


def _aad_scope(user_id) -> str:
    return f"user:{user_id}"


def valid_token_format(token: str) -> bool:
    return 20 <= len(token) <= 255 and token.isascii() and token.isprintable() and not any(c.isspace() for c in token)


async def get_meta(pool, user_id) -> dict:
    row = await pool.fetchrow(
        "SELECT last4, updated_at FROM git_credentials WHERE user_id = $1 AND provider = $2", user_id, PROVIDER,
    )
    if not row:
        return {"github_token_configured": False}
    return {"github_token_configured": True, "github_token_last4": row["last4"], "github_token_updated_at": row["updated_at"]}


async def save_token(pool, user_id, token: str) -> dict:
    """Cifra y hace upsert. Lanza CryptoError si la master key falta o es inválida."""
    ct, nonce, version = encrypt_secret(token, _aad_scope(user_id), PROVIDER)
    await pool.execute(
        "INSERT INTO git_credentials(user_id, provider, ciphertext, nonce, key_version, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "ON CONFLICT (user_id, provider) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
        "key_version = EXCLUDED.key_version, last4 = EXCLUDED.last4, updated_at = now()",
        user_id, PROVIDER, ct, nonce, version, token[-4:],
    )
    return await get_meta(pool, user_id)


async def delete_token(pool, user_id) -> bool:
    res = await pool.execute("DELETE FROM git_credentials WHERE user_id = $1 AND provider = $2", user_id, PROVIDER)
    return not res.endswith(" 0")


async def load_token(pool, user_id) -> str | None:
    """Descifra el token en memoria. None si no hay. Lanza CryptoError si no se puede descifrar."""
    row = await pool.fetchrow(
        "SELECT ciphertext, nonce, key_version FROM git_credentials WHERE user_id = $1 AND provider = $2",
        user_id, PROVIDER,
    )
    if not row:
        return None
    return decrypt_secret(row["ciphertext"], row["nonce"], row["key_version"], _aad_scope(user_id), PROVIDER)


__all__ = ["CryptoError", "master_key_ready", "valid_token_format", "get_meta", "save_token", "delete_token", "load_token"]
