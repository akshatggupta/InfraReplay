"""StoragePlugin — large payloads live here, never inline in the metadata DB."""

from abc import ABC, abstractmethod


class StoragePlugin(ABC):
    name: str = ""

    @abstractmethod
    async def put_blob(self, key: str, data: bytes) -> str:
        """Store `data` under `key`. Returns a locator string."""

    @abstractmethod
    async def get_blob(self, key: str) -> bytes:
        """Fetch a previously stored blob. Raises KeyError if absent."""
