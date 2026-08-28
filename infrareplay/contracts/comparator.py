"""ComparatorPlugin — decides MATCH / DIFFERENT / ERROR for one event pair."""

from abc import ABC, abstractmethod

from infrareplay.schema import ComparisonResult, Event


class ComparatorPlugin(ABC):
    name: str = ""

    # The event_type this comparator handles, e.g. "http.response".
    event_type: str = ""

    @abstractmethod
    def compare(self, original: Event, replayed: Event) -> ComparisonResult:
        """Compare a matched pair. Both events share `event_type`."""
