"""v3: two producers, one causal order."""

from infrareplay.recording import correlate
from infrareplay.schema import Event, EventType


def _event(event_type: EventType, ts: int, corr: str, eid: str) -> Event:
    return Event(
        recording_id="rec_1",
        event_id=eid,
        sequence=0,
        timestamp_ns=ts,
        duration_ns=1,
        event_type=event_type,
        service="demo_shop",
        correlation_id=corr,
    )


def test_out_of_order_arrivals_are_ordered_and_parented():
    # The proxy's response landed before the app's SQL did.
    arrived = [
        _event(EventType.HTTP_REQUEST, 100, "a", "req"),
        _event(EventType.HTTP_RESPONSE, 400, "a", "res"),
        _event(EventType.PG_QUERY, 200, "a", "qry"),
        _event(EventType.PG_RESULT, 300, "a", "rst"),
    ]

    ordered = correlate(arrived)

    assert [e.event_id for e in ordered] == ["req", "qry", "rst", "res"]
    assert [e.sequence for e in ordered] == [0, 1, 2, 3]

    by_id = {e.event_id: e for e in ordered}
    assert by_id["qry"].parent_event_id == "req"
    assert by_id["rst"].parent_event_id == "qry"
    assert by_id["res"].parent_event_id == "req"


def test_requests_stay_grouped_even_when_interleaved():
    arrived = [
        _event(EventType.HTTP_REQUEST, 100, "a", "req_a"),
        _event(EventType.HTTP_REQUEST, 110, "b", "req_b"),
        _event(EventType.PG_QUERY, 120, "b", "qry_b"),
        _event(EventType.PG_QUERY, 130, "a", "qry_a"),
    ]

    ordered = [e.event_id for e in correlate(arrived)]

    assert ordered == ["req_a", "qry_a", "req_b", "qry_b"]
