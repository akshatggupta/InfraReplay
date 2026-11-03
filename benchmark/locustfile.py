"""The benchmark workload: shoppers on the demo shop.

Three checkouts for every inventory browse, with a short think time — the
same checkout InfraReplay records in the demo, so the numbers describe the
workflow the product exists for, not a synthetic ping.
"""

from uuid import uuid4

from locust import HttpUser, between, task

CHECKOUT = "POST /api/v1/checkout"
BROWSE = "GET /api/v1/inventory"

_HEADERS = {
    "x-demo-user": "dana",
    # Sent so redaction does real work on every captured request.
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJiZW5jaCJ9.c2lnbmF0dXJl",
}


class Shopper(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(3)
    def checkout(self) -> None:
        self.client.post(
            "/api/v1/checkout",
            json={
                "cart_id": f"cart_{uuid4().hex[:8]}",
                "items": [{"sku": "AEROPRESS-GO", "qty": 1}],
                "payment_method": {"type": "card", "token": "tok_visa_bench", "last4": "4242"},
            },
            headers=_HEADERS,
            name=CHECKOUT,
        )

    @task(1)
    def browse(self) -> None:
        self.client.get("/api/v1/inventory", headers=_HEADERS, name=BROWSE)
