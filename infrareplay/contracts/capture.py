"""CapturePlugin — a source of Events.

Whether the source is synthetic (mock), a reverse proxy (http, v2), or DB
instrumentation (postgres, v3), the recording pipeline only ever sees this
interface.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from infrareplay.schema import Event


class CapturePlugin(ABC):
    name: str = ""
    event_types: list[str] = []

    @abstractmethod
    async def start(self, config: dict) -> None:
        """Begin capturing. `config` is plugin-specific."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop capturing and release resources."""

    @abstractmethod
    def events(self) -> AsyncIterator[Event]:
        """Yield captured events in `sequence` order."""
