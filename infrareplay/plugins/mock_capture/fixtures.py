"""Two canonical recordings of the demo_shop checkout flow.

Both model the same request-scoped workflow:

    POST /api/v1/checkout
      |- SELECT session + user        (auth middleware)
      |- BEGIN
      |- INSERT INTO orders
      |- INSERT INTO order_items      (2 line items)
      |- UPDATE inventory             (2 skus)
      |- INSERT INTO payments         (card gateway authorization)
      |- COMMIT / ROLLBACK
    -> HTTP 201 / HTTP 402

The buggy variant diverges only from the payment result onward. Replayed
against the fixed (clean) mock target it therefore produces real MATCH rows
(auth, order, line items, inventory) and real DIFFERENT rows (payment
result, final response).
"""

from enum import Enum
from uuid import uuid4

from infrareplay.schema import Event, EventType

# Wall-clock anchor for the synthetic stream (2026-08-29T10:00:00Z, ns).
_BASE_NS = 1_787_047_200_000_000_000
_MS = 1_000_000

SERVICE = "demo_shop"

ORDER_ID = "ord_9f2c1a7b3e00"
USER_ID = "usr_4a1c8e"
CUSTOMER_EMAIL = "dana.lang@example.com"
PAYMENT_REF = "pi_3Qx8Zk2eRfL0aQ"
PAYMENT_ROW_ID = "pay_2b9c07d1"

# Two line items; every downstream total is derived from these so the
# response body and the DB rows never drift apart.
_LINE_ITEMS = (
    {"sku": "AEROPRESS-GO", "name": "AeroPress Go", "qty": 1, "unit_cents": 3999},
    {"sku": "FILTER-350", "name": "Micro-filters (350 pack)", "qty": 2, "unit_cents": 899},
)
_SUBTOTAL_CENTS = sum(i["qty"] * i["unit_cents"] for i in _LINE_ITEMS)
_SHIPPING_CENTS = 500
_TAX_CENTS = 491
_TOTAL_CENTS = _SUBTOTAL_CENTS + _SHIPPING_CENTS + _TAX_CENTS


class Scenario(str, Enum):
    CLEAN = "clean"
    BUGGY = "buggy"


class _Stream:
    """Accumulates events with monotonic sequence numbers and a wall clock."""

    def __init__(self, recording_id: str) -> None:
        self._rid = recording_id
        self._corr = f"corr_{uuid4().hex[:12]}"
        self._trace = uuid4().hex
        self._seq = 0
        self._now_ns = _BASE_NS
        self.events: list[Event] = []

    def add(
        self,
        event_type: EventType,
        payload: dict,
        *,
        parent: str | None = None,
        gap_ms: float = 1,
        dur_ms: float = 1,
    ) -> Event:
        self._now_ns += int(gap_ms * _MS)

        event = Event(
            recording_id=self._rid,
            event_id=f"evt_{uuid4().hex[:12]}",
            parent_event_id=parent,
            sequence=self._seq,
            timestamp_ns=self._now_ns,
            duration_ns=int(dur_ms * _MS),
            event_type=event_type,
            service=SERVICE,
            correlation_id=self._corr,
            trace_id=self._trace,
            span_id=uuid4().hex[:16],
            payload=payload,
        )

        self._seq += 1
        self._now_ns += int(dur_ms * _MS)
        self.events.append(event)

        return event


# ------------------------------------------------------------------ payloads


def _request_payload() -> dict:
    return {
        "method": "POST",
        "path": "/api/v1/checkout",
        "http_version": "1.1",
        "client_ip": "203.0.113.42",
        "headers": {
            "host": "shop.demo.internal",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "DemoShop-Web/2.14.0 (+https://demo.shop)",
            "x-request-id": "req_a3f9c2b1e8d7",
            "idempotency-key": "chk_2026-08-29_dana_01",
            "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.checkout.redact-me",
            "cookie": "session=redact-me; ab_group=B",
        },
        "query": {},
        "body": {
            "cart_id": "cart_7b21f0",
            "items": [{"sku": i["sku"], "qty": i["qty"]} for i in _LINE_ITEMS],
            "shipping_address": {
                "name": "Dana Lang",
                "line1": "18 Harbour View",
                "city": "Bristol",
                "postcode": "BS1 4RN",
                "country": "GB",
            },
            "payment_method": {
                "type": "card",
                "token": "tok_live_x2Kd9pQ",
                "brand": "visa",
                "last4": "4242",
            },
        },
    }


def _payment_result(ok: bool) -> dict:
    state = "authorized" if ok else "declined"
    row = {
        "id": PAYMENT_ROW_ID,
        "order_id": ORDER_ID,
        "state": state,
        "provider": "stripe",
        "amount_cents": _TOTAL_CENTS,
    }

    if ok:
        row["provider_ref"] = PAYMENT_REF
    else:
        row["decline_code"] = "insufficient_funds"

    return {"rows_affected": 1, "rows": [row]}


def _response_payload(ok: bool) -> dict:
    if ok:
        return {
            "status": 201,
            "headers": {
                "content-type": "application/json",
                "location": f"/api/v1/orders/{ORDER_ID}",
            },
            "body": {
                "order_id": ORDER_ID,
                "status": "confirmed",
                "currency": "GBP",
                "totals": {
                    "subtotal_cents": _SUBTOTAL_CENTS,
                    "shipping_cents": _SHIPPING_CENTS,
                    "tax_cents": _TAX_CENTS,
                    "grand_total_cents": _TOTAL_CENTS,
                },
                "payment": {
                    "state": "authorized",
                    "provider": "stripe",
                    "reference": PAYMENT_REF,
                },
                "fulfilment": {"carrier": "royal-mail", "eta_days": 3},
                "line_items": [dict(i) for i in _LINE_ITEMS],
            },
        }

    return {
        "status": 402,
        "headers": {"content-type": "application/problem+json"},
        "body": {
            "type": "https://demo.shop/errors/payment-declined",
            "title": "Payment was declined",
            "status": 402,
            "order_id": ORDER_ID,
            "payment": {
                "state": "declined",
                "provider": "stripe",
                "decline_code": "insufficient_funds",
            },
            "hint": "Ask the customer to try a different card.",
        },
    }


# -------------------------------------------------------------------- steps


def _pg_step(
    stream: _Stream,
    request: Event,
    sql: str,
    params: list,
    result: dict,
    *,
    gap_ms: float,
    query_ms: float,
) -> None:
    query = stream.add(
        EventType.PG_QUERY,
        {"sql": sql, "params": params},
        parent=request.event_id,
        gap_ms=gap_ms,
        dur_ms=query_ms,
    )

    stream.add(
        EventType.PG_RESULT,
        result,
        parent=query.event_id,
        gap_ms=0,
        dur_ms=0.4,
    )


def build(recording_id: str, scenario: Scenario) -> list[Event]:
    ok = scenario is Scenario.CLEAN
    stream = _Stream(recording_id)

    request = stream.add(
        EventType.HTTP_REQUEST, _request_payload(), gap_ms=0, dur_ms=141
    )

    _pg_step(
        stream,
        request,
        "SELECT s.user_id, u.email, u.loyalty_tier\n"
        "  FROM sessions s JOIN users u ON u.id = s.user_id\n"
        " WHERE s.token_hash = $1 AND s.expires_at > now()",
        ["sha256:9c1f0b…a7"],
        {
            "rows_affected": 1,
            "rows": [
                {
                    "user_id": USER_ID,
                    "email": CUSTOMER_EMAIL,
                    "loyalty_tier": "gold",
                }
            ],
        },
        gap_ms=3,
        query_ms=4,
    )

    stream.add(
        EventType.PG_QUERY,
        {"sql": "BEGIN", "params": []},
        parent=request.event_id,
        gap_ms=1,
        dur_ms=0.3,
    )

    _pg_step(
        stream,
        request,
        "INSERT INTO orders (id, user_id, currency, subtotal_cents,\n"
        "                    shipping_cents, tax_cents, total_cents, status)\n"
        "VALUES ($1, $2, 'GBP', $3, $4, $5, $6, 'pending')",
        [
            ORDER_ID,
            USER_ID,
            _SUBTOTAL_CENTS,
            _SHIPPING_CENTS,
            _TAX_CENTS,
            _TOTAL_CENTS,
        ],
        {"rows_affected": 1, "rows": [{"id": ORDER_ID, "status": "pending"}]},
        gap_ms=1,
        query_ms=8,
    )

    _pg_step(
        stream,
        request,
        "INSERT INTO order_items (order_id, sku, qty, unit_cents)\n"
        "VALUES ($1, $2, $3, $4), ($1, $5, $6, $7)",
        [
            ORDER_ID,
            _LINE_ITEMS[0]["sku"],
            _LINE_ITEMS[0]["qty"],
            _LINE_ITEMS[0]["unit_cents"],
            _LINE_ITEMS[1]["sku"],
            _LINE_ITEMS[1]["qty"],
            _LINE_ITEMS[1]["unit_cents"],
        ],
        {"rows_affected": 2, "rows": []},
        gap_ms=1,
        query_ms=6,
    )

    _pg_step(
        stream,
        request,
        "UPDATE inventory SET available = available - c.qty\n"
        "  FROM (VALUES ($1::text, $2), ($3::text, $4)) AS c(sku, qty)\n"
        " WHERE inventory.sku = c.sku",
        [
            _LINE_ITEMS[0]["sku"],
            _LINE_ITEMS[0]["qty"],
            _LINE_ITEMS[1]["sku"],
            _LINE_ITEMS[1]["qty"],
        ],
        {"rows_affected": 2, "rows": []},
        gap_ms=1,
        query_ms=7,
    )

    _pg_step(
        stream,
        request,
        "INSERT INTO payments (id, order_id, provider, amount_cents, state)\n"
        "VALUES ($1, $2, 'stripe', $3, $4)\n"
        "RETURNING id, order_id, state, provider, amount_cents",
        [PAYMENT_ROW_ID, ORDER_ID, _TOTAL_CENTS, "authorized" if ok else "declined"],
        _payment_result(ok),
        gap_ms=2,
        query_ms=53,
    )

    stream.add(
        EventType.PG_QUERY,
        {"sql": "COMMIT" if ok else "ROLLBACK", "params": []},
        parent=request.event_id,
        gap_ms=1,
        dur_ms=2.5 if ok else 0.6,
    )

    stream.add(
        EventType.HTTP_RESPONSE,
        _response_payload(ok),
        parent=request.event_id,
        gap_ms=2,
        dur_ms=1,
    )

    return stream.events
