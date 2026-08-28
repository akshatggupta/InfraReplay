"""FilesystemStoragePlugin — writes blobs under a configurable root."""

from pathlib import Path

from infrareplay import config
from infrareplay.contracts import StoragePlugin

_LOCATOR_PREFIX = "file://"


class FilesystemStoragePlugin(StoragePlugin):
    name = "storage_fs"

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or config.BLOB_ROOT)

    async def put_blob(self, key: str, data: bytes) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        return f"{_LOCATOR_PREFIX}{path}"

    async def get_blob(self, key: str) -> bytes:
        path = self._path_for(key)

        if not path.is_file():
            raise KeyError(key)

        return path.read_bytes()

    def _path_for(self, key: str) -> Path:
        # Keys are slash-delimited; keep them inside the root.
        safe = Path(key.strip("/"))

        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"unsafe blob key: {key!r}")

        return self._root / safe
