"""AES-256-GCM para claves de proveedor. La master key vive solo en el entorno."""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

CURRENT_KEY_VERSION = 1


class CryptoError(RuntimeError):
    pass


def _load_master_key() -> bytes:
    raw = (settings.master_key or "").strip()
    if not raw:
        raise CryptoError("ASKBOX_MASTER_KEY no configurada")
    candidates = []
    try:
        candidates.append(bytes.fromhex(raw))
    except ValueError:
        pass
    try:
        candidates.append(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError):
        pass
    for key in candidates:
        if len(key) == 32:
            return key
    raise CryptoError("ASKBOX_MASTER_KEY debe ser 32 bytes en hex (64 chars) o base64")


def master_key_ready() -> bool:
    try:
        _load_master_key()
        return True
    except CryptoError:
        return False


def _aad(project_id: str, provider: str) -> bytes:
    return f"askbox:{project_id}:{provider}".encode()


def encrypt_secret(plaintext: str, project_id: str, provider: str) -> tuple[bytes, bytes, int]:
    """Devuelve (ciphertext, nonce, key_version)."""
    key = _load_master_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(project_id, provider))
    return ct, nonce, CURRENT_KEY_VERSION


def decrypt_secret(ciphertext: bytes, nonce: bytes, key_version: int, project_id: str, provider: str) -> str:
    if key_version != CURRENT_KEY_VERSION:
        raise CryptoError(f"key_version {key_version} no soportada")
    key = _load_master_key()
    try:
        return AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), _aad(project_id, provider)).decode()
    except InvalidTag as exc:
        raise CryptoError("no se pudo descifrar (master key o datos no coinciden)") from exc
