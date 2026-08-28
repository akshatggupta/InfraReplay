"""PostgresComparator — compares replayed query results to the originals."""

from infrareplay.contracts import ComparatorPlugin
from infrareplay.schema import ComparisonCategory, ComparisonResult, Event

_ROWS_AFFECTED = "rows_affected"
_ROWS = "rows"
_ERROR = "error"


class PostgresComparator(ComparatorPlugin):
    name = "postgres_comparator"
    event_type = "postgres.result"

    def compare(self, original: Event, replayed: Event) -> ComparisonResult:
        result = ComparisonResult(
            replay_recording_id=replayed.recording_id,
            original_event_id=original.event_id,
            replayed_event_id=replayed.event_id,
            event_type=self.event_type,
            category=ComparisonCategory.MATCH,
        )

        if _ERROR in replayed.payload:
            result.category = ComparisonCategory.ERROR
            result.diff = {_ERROR: replayed.payload[_ERROR]}
            return result

        diff: dict = {}

        for key in (_ROWS_AFFECTED, _ROWS):
            orig = original.payload.get(key)
            new = replayed.payload.get(key)

            if orig != new:
                diff[key] = {"original": orig, "replayed": new}

        if diff:
            result.category = ComparisonCategory.DIFFERENT
            result.diff = diff

        return result
