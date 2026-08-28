"""HttpComparator — compares replayed HTTP responses to the originals."""

from infrareplay.contracts import ComparatorPlugin
from infrareplay.schema import ComparisonCategory, ComparisonResult, Event

_STATUS = "status"
_BODY = "body"
_ERROR = "error"


class HttpComparator(ComparatorPlugin):
    name = "http_comparator"
    event_type = "http.response"

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

        orig_status = original.payload.get(_STATUS)
        new_status = replayed.payload.get(_STATUS)

        if orig_status != new_status:
            diff[_STATUS] = {"original": orig_status, "replayed": new_status}

        orig_body = original.payload.get(_BODY)
        new_body = replayed.payload.get(_BODY)

        if orig_body != new_body:
            diff[_BODY] = {"original": orig_body, "replayed": new_body}

        if diff:
            result.category = ComparisonCategory.DIFFERENT
            result.diff = diff

        return result
