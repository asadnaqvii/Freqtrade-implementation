"""
AES-256-GCM encryption for KuCoin API keys at rest.
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from whatsapp_bot.config import settings


def _get_key() -> bytes:
    """Return 32-byte AES key from hex env var, or generate one for dev."""
    hex_key = settings.encryption_key
    if hex_key:
        return bytes.fromhex(hex_key)
    # Dev fallback — NOT secure for production
    return b"\x00" * 32


def encrypt(plaintext: str) -> str:
    """Encrypt a string → base64-encoded ciphertext (nonce || ciphertext || tag)."""
    key = _get_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(token: str) -> str:
    """Decrypt a base64-encoded token back to plaintext."""
    key = _get_key()
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()
