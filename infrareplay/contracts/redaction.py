"""RedactionPlugin — strips known-sensitive fields before an Event is stored."""

from abc import ABC, abstractmethod

from infrareplay.schema import Event


class RedactionPlugin(ABC):
    name: str = ""

    @abstractmethod
    def redact(self, event: Event) -> Event:
        """Return a copy of `event` with sensitive values masked."""
