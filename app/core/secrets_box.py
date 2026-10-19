"""
At-rest encryption for tenant-supplied secrets (BYOC Exotel token, custom SMTP password).
AES-256-GCM, stored as "enc:v1:<base64(nonce|ciphertext)>". Values without the prefix are
treated as legacy plaintext and returned unchanged, so existing rows keep working.
"""
import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_PREFIX = "enc:v1:"


def _key() -> bytes:
    material = settings.SECRETS_ENCRYPTION_KEY or f"qwickcall-secrets::{settings.JWT_SECRET_KEY}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value or value.startswith(_PREFIX):
        return value
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, value.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception:
        return None
