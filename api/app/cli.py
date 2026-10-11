"""CLI de operador (dentro del contenedor): python -m app.cli <comando>.

  ensure-user EMAIL   crea el usuario si no existe (contraseña aleatoria inutilizable) y
                      imprime por stdout un enlace de reset de 24 h para que elija la suya.
  reset-link EMAIL    emite un enlace de reset de 24 h para un usuario existente.

El enlace es un secreto: redirigir stdout a un archivo con permisos 600, no pegarlo en chats/logs.
"""
from __future__ import annotations

import asyncio
import secrets
import sys

from app.config import settings
from app.db import create_pool, run_migrations
from app.routers.auth import _EMAIL, issue_reset_token
from app.security import hash_password


async def _main(cmd: str, email: str) -> int:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        print("email no válido", file=sys.stderr)
        return 2
    pool = await create_pool()
    try:
        await run_migrations(pool)
        uid = await pool.fetchval("SELECT id FROM users WHERE email = $1", email)
        if uid is None:
            if cmd != "ensure-user":
                print("usuario no existe", file=sys.stderr)
                return 1
            uid = await pool.fetchval(
                "INSERT INTO users(email, password_hash) VALUES ($1, $2) RETURNING id",
                email, hash_password(secrets.token_urlsafe(32)),
            )
            print("usuario creado", file=sys.stderr)
        token = await issue_reset_token(pool, uid, 60 * 24)
        print(f"{settings.public_url}/reset?token={token}")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"ensure-user", "reset-link"}:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2])))
