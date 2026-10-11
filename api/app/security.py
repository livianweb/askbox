"""Hash de contraseñas (bcrypt) y tokens de sesión."""
from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt

# Hash ficticio para igualar tiempos cuando el usuario no existe.
_DUMMY_HASH = bcrypt.hashpw(b"askbox-dummy", bcrypt.gensalt(rounds=12)).decode()


def _prehash(password: str) -> bytes:
    # sha256 + base64 evita el límite de 72 bytes de bcrypt
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        ok = bcrypt.checkpw(_prehash(password), (password_hash or _DUMMY_HASH).encode())
    except Exception:
        return False
    return ok and password_hash is not None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
