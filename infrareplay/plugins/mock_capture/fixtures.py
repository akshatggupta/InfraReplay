"""Two canonical recordings: one clean, one where the payment step fails.

Both model the same workflow:

    POST /orders
      ├─ INSERT INTO orders           (postgres)
      ├─ UPDATE inventory             (postgres)
      └─ SELECT ... FROM payments     (postgres)
    -> HTTP response

The buggy variant differs only from the payment SELECT onward, so a replay
against a fixed (clean) target yields both MATCH rows (the order insert,
the inventory update) and DIFFERENT rows (payment result, final response).
"""

from enum import Enum
from uuid import uuid4

from infrareplay.schema import Event, EventType

# Wall-clock anchor for the synthetic stream (2026-08-29T10:00:00Z, ns).
_BASE_NS = 1_787_047_200_000_000_000

_MS = 1_000_000

SERVICE = "demo_shop"

CLEAN_ORDER_ID = "ord_1a2b3c"


class Scenario(str, Enum):
    CLEAN = "clean"
    BUGGY = "buggy"


def _http_request(rid: str, corr: str) -> Event:
    return Event(
        recording_id=rid,
        event_id=f"evt_{uuid4().hex[:12]}",
        sequence=0,
        timestamp_ns=_BASE_NS,
        duration_ns=48 * _MS,
        event_type=EventType.HTTP_REQUEST,
        service=SERVICE,
        correlation_id=corr,
        payload={
            "method": "POST",
            "path": "/orders",
            "headers": {
                "content-type": "application/json",
                "authorization": "Bearer eyJhbGciOi...redact-me",
            },
            "query": {},
            "body": {"sku": "WIDGET-1", "qty": 2, "customer_id": "cus_42"},
        },
    )


def _pg_pair(
    rid: str,
    corr: str,
    parent: str,
    start_seq: int,
    at_ns: int,
    sql: str,
    params: list,
    result: dict,
) -> list[Event]:
    query = Event(
        recording_id=rid,
        event_id=f"evt_{uuid4().hex[:12]}",
        parent_event_id=parent,
        sequence=start_seq,
        timestamp_ns=at_ns,
        duration_ns=6 * _MS,
        event_type=EventType.PG_QUERY,
        service=SERVICE,
        correlation_id=corr,
        payload={"sql": sql, "params": params},
    )

    res = Event(
        recording_id=rid,
        event_id=f"evt_{uuid4().hex[:12]}",
        parent_event_id=query.event_id,
        sequence=start_seq + 1,
        timestamp_ns=at_ns + 6 * _MS,
        duration_ns=1 * _MS,
        event_type=EventType.PG_RESULT,
        service=SERVICE,
        correlation_id=corr,
        payload=result,
    )

    return [query, res]


def build(recording_id: str, scenario: Scenario) -> list[Event]:
    corr = f"corr_{uuid4().hex[:12]}"

    req = _http_request(recording_id, corr)
    events: list[Event] = [req]

    events += _pg_pair(
        recording_id, corr, req.event_id, 1, _BASE_NS + 8 * _MS,
        "INSERT INTO orders (id, sku, qty, customer_id) VALUES ($1,$2,$3,$4)",
        [CLEAN_ORDER_ID, "WIDGET-1", 2, "cus_42"],
        {"rows_affected": 1, "rows": [{"id": CLEAN_ORDER_ID}]},
    )

    events += _pg_pair(
        recording_id, corr, req.event_id, 3, _BASE_NS + 16 * _MS,
        "UPDATE inventory SET available = available - $1 WHERE sku = $2",
        [2, "WIDGET-1"],
        {"rows_affected": 1, "rows": []},
    )

    payment_ok = scenario is Scenario.CLEAN

    events += _pg_pair(
        recording_id, corr, req.event_id, 5, _BASE_NS + 24 * _MS,
        "SELECT status FROM payments WHERE order_id = $1",
        [CLEAN_ORDER_ID],
        {
            "rows_affected": 1 if payment_ok else 0,
            "rows": [{"status": "captured"}] if payment_ok else [],
        },
    )

    events.append(
        Event(
            recording_id=recording_id,
            event_id=f"evt_{uuid4().hex[:12]}",
            parent_event_id=req.event_id,
            sequence=7,
            timestamp_ns=_BASE_NS + 40 * _MS,
            duration_ns=2 * _MS,
            event_type=EventType.HTTP_RESPONSE,
            service=SERVICE,
            correlation_id=corr,
            payload={
                "status": 201 if payment_ok else 402,
                "headers": {"content-type": "application/json"},
                "body": (
                    {"order_id": CLEAN_ORDER_ID, "status": "confirmed"}
                    if payment_ok
                    else {"order_id": CLEAN_ORDER_ID, "status": "payment_failed"}
                ),
            },
        )
    )

    return events
