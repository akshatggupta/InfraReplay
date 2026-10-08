"""EncryptedStoragePlugin — another storage plugin, with Fernet in between.

    put_blob:  plaintext ─> Fernet token ─> MAGIC + token ─> inner.put_blob
    get_blob:  inner.get_blob ─> check MAGIC ─> decrypt (fails on a wrong key)

It wraps a backend resolved by name through the registry rather than
writing files itself, so encryption composes with whatever storage is
installed instead of being reimplemented inside each one.
"""

from infrareplay.contracts import StoragePlugin
from infrareplay.plugins.registry import get_registry
from infrareplay.security.keys import EncryptionKeyError, InvalidToken, cipher

# Marks a blob as ours, so a plaintext blob is reported as one rather than
# as a decryption failure.
MAGIC = b"IRENC1\n"

_INNER = "storage_fs"


class EncryptedStoragePlugin(StoragePlugin):
    name = "storage_encrypted"

    def __init__(self, inner: StoragePlugin | None = None, key: str | None = None) -> None:
        self._cipher = cipher(key)
        self._inner = inner or get_registry().storage(_INNER)()

    async def put_blob(self, key: str, data: bytes) -> str:
        return await self._inner.put_blob(key, MAGIC + self._cipher.encrypt(data))

    async def get_blob(self, key: str) -> bytes:
        stored = await self._inner.get_blob(key)

        if not stored.startswith(MAGIC):
            raise ValueError(f"blob {key!r} was not written by encrypted storage")

        try:
            return self._cipher.decrypt(stored[len(MAGIC):])
        except InvalidToken as exc:
            raise EncryptionKeyError(
                f"cannot decrypt blob {key!r}: wrong key, or the blob was altered"
            ) from exc
