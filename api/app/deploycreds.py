"""Token de Cloudflare Tunnel cifrado en BD (tabla `git_credentials`, provider `cloudflare_tunnel`)."""
from __future__ import annotations

from app.crypto import CryptoError, decrypt_secret, encrypt_secret, master_key_ready

PROVIDER = "cloudflare_tunnel"
REPO_SCOPE = "deploy"


def _aad_scope(user_id) -> str:
    return f"user:{user_id}:deploy:tunnel"


def valid_token_format(token: str) -> bool:
    # Tokens de connector suelen ser largos JWT-like; sin espacios ni caracteres de control.
    return 40 <= len(token) <= 2000 and token.isascii() and token.isprintable() and not any(c.isspace() for c in token)


async def get_meta(pool, user_id) -> dict:
    row = await pool.fetchrow(
        "SELECT last4, updated_at FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3",
        user_id, PROVIDER, REPO_SCOPE,
    )
    if not row:
        return {"tunnel_token_configured": False}
    return {
        "tunnel_token_configured": True,
        "tunnel_token_last4": row["last4"],
        "tunnel_token_updated_at": row["updated_at"],
    }


async def save_token(pool, user_id, token: str) -> dict:
    ct, nonce, version = encrypt_secret(token, _aad_scope(user_id), PROVIDER)
    await pool.execute(
        "INSERT INTO git_credentials(user_id, provider, repo, ciphertext, nonce, key_version, last4) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (user_id, provider, repo) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
        "key_version = EXCLUDED.key_version, last4 = EXCLUDED.last4, updated_at = now()",
        user_id, PROVIDER, REPO_SCOPE, ct, nonce, version, token[-4:],
    )
    return await get_meta(pool, user_id)


async def delete_token(pool, user_id) -> bool:
    res = await pool.execute(
        "DELETE FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3",
        user_id, PROVIDER, REPO_SCOPE,
    )
    return not res.endswith(" 0")


async def load_token(pool, user_id) -> str | None:
    row = await pool.fetchrow(
        "SELECT ciphertext, nonce, key_version FROM git_credentials WHERE user_id = $1 AND provider = $2 AND repo = $3",
        user_id, PROVIDER, REPO_SCOPE,
    )
    if not row:
        return None
    return decrypt_secret(row["ciphertext"], row["nonce"], row["key_version"], _aad_scope(user_id), PROVIDER)


__all__ = ["CryptoError", "master_key_ready", "valid_token_format", "get_meta", "save_token", "delete_token", "load_token"]
