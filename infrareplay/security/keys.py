"""Encryption keys for blobs at rest.

Fernet: AES-128-CBC with an HMAC-SHA256, authenticated, url-safe base64 key.
A wrong or tampered key fails loudly on read instead of returning garbage.
"""

from cryptography.fernet import Fernet, InvalidToken

from infrareplay import config


class EncryptionKeyError(RuntimeError):
    """No usable encryption key."""


def generate_key() -> str:
    return Fernet.generate_key().decode()


def cipher(key: str | None = None) -> Fernet:
    material = key if key is not None else config.ENCRYPTION_KEY

    if not material:
        raise EncryptionKeyError(
            "encrypted storage needs INFRAREPLAY_ENCRYPTION_KEY; "
            "generate one with `infractl security keygen`"
        )

    try:
        return Fernet(material.encode())
    except ValueError as exc:
        raise EncryptionKeyError(f"INFRAREPLAY_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


__all__ = ["EncryptionKeyError", "InvalidToken", "cipher", "generate_key"]
