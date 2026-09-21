"""v5: what the comparison must ignore, and what it must never ignore."""

from infrareplay.comparison.normalize import ID, TIMESTAMP, UUID, normalize


def test_generated_ids_and_clocks_collapse_to_shapes():
    out = normalize(
        {
            "order_id": "ord_9f2c1a7b3e00",
            "reference": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "created_at": "2026-09-21T10:00:00.412Z",
            "updated_at": "2026-09-21 10:00:00.412",
        }
    )

    assert out == {
        "order_id": ID,
        "reference": UUID,
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
    }


def test_volatile_headers_are_dropped():
    out = normalize({"date": "Mon, 21 Sep 2026 10:00:00 GMT", "content-type": "application/json"})

    assert out == {"content-type": "application/json"}


def test_real_values_survive():
    payload = {"status": "declined", "decline_code": "insufficient_funds", "total_cents": 4978}

    assert normalize(payload) == payload


def test_nested_structures_are_walked():
    out = normalize({"rows": [{"id": "pay_2b9c07d1", "state": "authorized"}]})

    assert out == {"rows": [{"id": ID, "state": "authorized"}]}
