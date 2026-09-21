"""Merge a multi-source event stream into one causal order.

A live capture has two independent producers — the HTTP proxy (in the
InfraReplay process) and the SQL listener (inside the target app) — so the
order events *arrive* in is not the order they *happened* in.
`correlation_id` says which request an event belongs to; `timestamp_ns`
orders the events inside that request:

    corr_a  http.request ─┬─ postgres.query ── postgres.result
                          ├─ postgres.query ── postgres.result
                          └─ http.response

Recorded parent links are never overwritten; correlation only fills the
gaps a live producer could not know about.
"""

from infrareplay.schema import Event, EventType


def correlate(events: list[Event]) -> list[Event]:
    """Return `events` re-sequenced and re-parented, request group by group."""

    ordered: list[Event] = []
    sequence = 0

    for group in _groups(events):
        for event in _link(group):
            ordered.append(event.model_copy(update={"sequence": sequence}))
            sequence += 1

    return ordered


def _groups(events: list[Event]) -> list[list[Event]]:
    """Split into per-correlation_id groups, each in wall-clock order."""

    by_correlation: dict[str, list[Event]] = {}

    for event in events:
        by_correlation.setdefault(event.correlation_id, []).append(event)

    groups = [
        sorted(g, key=lambda e: (e.timestamp_ns, e.sequence))
        for g in by_correlation.values()
    ]

    return sorted(groups, key=lambda g: (g[0].timestamp_ns, g[0].sequence))


def _link(group: list[Event]) -> list[Event]:
    """Fill missing parent links: DB work hangs off the request that caused it."""

    root = next((e for e in group if e.event_type is EventType.HTTP_REQUEST), None)
    last_query: Event | None = None

    linked: list[Event] = []

    for event in group:
        parent = event.parent_event_id or _parent_for(event, root, last_query)
        linked.append(event.model_copy(update={"parent_event_id": parent}))

        if event.event_type is EventType.PG_QUERY:
            last_query = event

    return linked


def _parent_for(event: Event, root: Event | None, last_query: Event | None) -> str | None:
    if event.event_type is EventType.HTTP_REQUEST:
        return None

    if event.event_type is EventType.PG_RESULT and last_query is not None:
        return last_query.event_id

    return root.event_id if root else None
