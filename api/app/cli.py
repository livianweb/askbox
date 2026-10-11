"""CLI de operador (dentro del contenedor): python -m app.cli <comando>.

  ensure-user EMAIL   crea el usuario si no existe (contraseña aleatoria inutilizable) y
                      imprime por stdout un enlace de reset de 24 h para que elija la suya.
  reset-link EMAIL    emite un enlace de reset de 24 h para un usuario existente.
  git-setup EMAIL     crea/conecta remotos GitHub (token cifrado en BD) y pushea askbox + kit.

El enlace de reset es un secreto: redirigir stdout a un archivo con permisos 600, no pegarlo en chats/logs.
git-setup imprime solo URLs y estado (nunca tokens).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys

from app.config import settings
from app.db import create_pool, run_migrations
from app.routers.auth import _EMAIL, issue_reset_token
from app.routers.git_settings import RemoteIn, _create_with_token
from app import gitcreds, gitops
from app.routers.git_settings import PRIMARY_REPO
from app.security import hash_password


async def _git_setup(email: str) -> int:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        print("email no válido", file=sys.stderr)
        return 2
    pool = await create_pool()
    try:
        uid = await pool.fetchval("SELECT id FROM users WHERE email = $1", email)
        if uid is None:
            print("usuario no existe", file=sys.stderr)
            return 1
        specs = [
            ("askbox", settings.repo_path, "askbox", "private"),
            ("cursor-agent-orchestrator", settings.extra_repos.get("cursor-agent-orchestrator", ""), "cursor-agent-orchestrator", "public"),
        ]
        report: list[dict] = []
        for repo_key, path, slug, visibility in specs:
            entry = {"repo": repo_key, "slug": slug, "visibility": visibility}
            if not path or not gitops.is_repo_available(path):
                entry["error"] = "repo_no_accesible"
                report.append(entry)
                continue
            token = await gitcreds.load_token(pool, uid, repo_key)
            if not token:
                entry["error"] = "github_token_not_configured"
                report.append(entry)
                continue
            body = RemoteIn(name="origin", create=True, slug=slug, visibility=visibility, push=True)
            try:
                info = await _create_with_token(path, body, visibility, token, push_prefs=(repo_key == PRIMARY_REPO))
                entry["html_url"] = info.get("html_url")
                entry["private"] = info.get("private")
                entry["connected_existing_remote"] = info.get("connected_existing_remote", False)
            except Exception as exc:  # noqa: BLE001 — CLI operador: mensaje ya saneado en HTTPException detail
                from fastapi import HTTPException

                if isinstance(exc, HTTPException):
                    detail = exc.detail
                    if isinstance(detail, dict):
                        entry["error"] = detail.get("message") or detail.get("code") or "error"
                    else:
                        entry["error"] = str(detail)
                else:
                    entry["error"] = type(exc).__name__
            report.append(entry)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all("error" not in e for e in report) else 1
    finally:
        await pool.close()


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
    if len(sys.argv) != 3 or sys.argv[1] not in {"ensure-user", "reset-link", "git-setup"}:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "git-setup":
        sys.exit(asyncio.run(_git_setup(sys.argv[2])))
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2])))
