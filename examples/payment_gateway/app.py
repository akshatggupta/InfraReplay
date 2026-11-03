"""The payment gateway — the external service the demo shop charges cards on.

    uvicorn examples.payment_gateway.app:app --port 4000

A stand-in for a provider like Stripe, run as its own service so the shop
makes a real outbound HTTP call for every payment, with an API key, the way
a production shop does. Its ledger is the evidence for v11: every charge it
ever accepted is listed at GET /v1/charges, so "a replay charged nobody" is
something you can count, not something you have to take on trust.

    POST /v1/charges     charge a card; 200 succeeded, 402 declined
    GET  /v1/charges     the ledger: how many charges, and the latest
    POST /v1/reset       empty the ledger

Declines are deterministic, so a replay can reproduce them: a card token
starting `tok_decline` is refused, and so is any amount over the limit.

Environment:
    PAYMENT_GATEWAY_KEY   the API key callers must present (Bearer)
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_PAYMENT_REQUIRED = 402
HTTP_BAD_REQUEST = 400

DEFAULT_KEY = "gw_test_key_demo"
DECLINE_PREFIX = "tok_decline"
LIMIT_CENTS = 50_000

_LEDGER_PAGE = 20


def gateway_key() -> str:
    return os.environ.get("PAYMENT_GATEWAY_KEY", DEFAULT_KEY)


def create_app() -> FastAPI:
    app = FastAPI(title="payment_gateway", version="1.0.0")
    ledger: list[dict] = []

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "charges": len(ledger)}

    @app.post("/v1/charges")
    async def charge(request: Request) -> JSONResponse:
        if request.headers.get("authorization") != f"Bearer {gateway_key()}":
            return _error(HTTP_UNAUTHORIZED, "invalid_api_key", "No valid API key provided.")

        body = await request.json()
        amount = body.get("amount_cents")
        source = str(body.get("source", ""))

        if not isinstance(amount, int) or amount <= 0:
            return _error(HTTP_BAD_REQUEST, "invalid_amount", "amount_cents must be a positive integer.")

        declined = source.startswith(DECLINE_PREFIX) or amount > LIMIT_CENTS
        record = {
            "id": f"ch_{uuid4().hex[:16]}",
            "status": "declined" if declined else "succeeded",
            "amount_cents": amount,
            "currency": body.get("currency", "gbp"),
            "order_id": (body.get("metadata") or {}).get("order_id"),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        # Declines are recorded too: a provider's ledger keeps every attempt.
        ledger.append(record)

        if declined:
            return JSONResponse(
                {
                    "error": {
                        "type": "card_error",
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "charge": record["id"],
                    }
                },
                status_code=HTTP_PAYMENT_REQUIRED,
            )

        return JSONResponse(record, status_code=HTTP_OK)

    @app.get("/v1/charges")
    async def charges() -> dict:
        return {"count": len(ledger), "latest": ledger[-_LEDGER_PAGE:][::-1]}

    @app.post("/v1/reset")
    async def reset() -> dict:
        ledger.clear()
        return {"reset": True}

    return app


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": "invalid_request_error", "code": code, "message": message}}, status_code=status)


app = create_app()
