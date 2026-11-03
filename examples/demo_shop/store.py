"""Checkout: the flow InfraReplay records, and the bug it exists to catch.

    POST /api/v1/checkout
      SELECT users             who is buying
      SELECT inventory         what is in stock      <- read half of the race
      INSERT orders
      INSERT order_items
      UPDATE inventory         reserve the stock     <- write half of the race
      POST /v1/charges         the card, at the payment gateway (real HTTP)
      INSERT payments
      COMMIT

Reserve.READ_THEN_WRITE (default — the bug):
    availability is read, decided on in Python, and written back as an
    absolute value. Two checkouts interleaving between the read and the
    write both believe the last unit is theirs, and the shop oversells.

Reserve.ATOMIC (DEMO_SHOP_LOCK_INVENTORY=1 — the fix):
    UPDATE inventory SET available = available - :qty WHERE available >= :qty
    The loser's UPDATE matches no row, and its checkout is rejected with 409.
"""

import asyncio
from enum import Enum
from uuid import uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from examples.demo_shop.models import Inventory, Order, OrderItem, Payment, User

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_UNAUTHORIZED = 401
HTTP_PAYMENT_REQUIRED = 402
HTTP_CONFLICT = 409
HTTP_BAD_GATEWAY = 502

SHIPPING_CENTS = 500
TAX_RATE = 0.12

# Payments are decided by the gateway service, not here: the shop sends the
# card to POST /v1/charges and records what came back.
PROVIDER = "gateway"
CHARGES_PATH = "/v1/charges"


class Reserve(str, Enum):
    READ_THEN_WRITE = "read_then_write"
    ATOMIC = "atomic"


async def checkout(
    session: AsyncSession,
    *,
    handle: str,
    body: dict,
    reserve: Reserve,
    race_window_s: float,
    payments: httpx.AsyncClient,
) -> tuple[int, dict]:
    user = await session.scalar(select(User).where(User.handle == handle))

    if user is None:
        return HTTP_UNAUTHORIZED, {"error": "unknown_user", "handle": handle}

    lines, missing = await _price(session, body.get("items", []))

    if missing:
        return HTTP_CONFLICT, {"error": "unknown_sku", "skus": missing}

    subtotal = sum(line["qty"] * line["unit_cents"] for line in lines)
    tax = int(subtotal * TAX_RATE)
    total = subtotal + SHIPPING_CENTS + tax

    order_id = f"ord_{uuid4().hex[:12]}"

    session.add(
        Order(id=order_id, user_id=user.id, status="pending", total_cents=total)
    )

    for line in lines:
        session.add(
            OrderItem(
                order_id=order_id,
                sku=line["sku"],
                qty=line["qty"],
                unit_cents=line["unit_cents"],
            )
        )

    await session.flush()

    short = await _reserve(session, lines, reserve, race_window_s)

    if short:
        await session.rollback()
        return HTTP_CONFLICT, {"error": "out_of_stock", "skus": short, "order_id": order_id}

    payment = await _charge(payments, order_id, total, body.get("payment_method", {}))

    if payment is None:
        await session.rollback()
        return HTTP_BAD_GATEWAY, {"error": "payment_gateway_unavailable", "order_id": order_id}

    session.add(payment)

    if payment.state == "declined":
        # The card was refused: release what was reserved, keep the record.
        await _release(session, lines)
        await session.execute(
            update(Order).where(Order.id == order_id).values(status="payment_declined")
        )
        await session.commit()
        return HTTP_PAYMENT_REQUIRED, _declined_body(order_id, payment)

    await session.execute(
        update(Order).where(Order.id == order_id).values(status="confirmed")
    )
    await session.commit()

    return HTTP_CREATED, _confirmed_body(order_id, lines, subtotal, tax, total, payment)


# ---------------------------------------------------------------------- stock


async def _price(session: AsyncSession, items: list[dict]) -> tuple[list[dict], list[str]]:
    lines: list[dict] = []
    missing: list[str] = []

    for item in items:
        row = await session.scalar(
            select(Inventory).where(Inventory.sku == item["sku"])
        )

        if row is None:
            missing.append(item["sku"])
            continue

        lines.append(
            {
                "sku": row.sku,
                "name": row.name,
                "qty": int(item.get("qty", 1)),
                "unit_cents": row.unit_cents,
                "available_seen": row.available,
            }
        )

    return lines, missing


async def _reserve(
    session: AsyncSession,
    lines: list[dict],
    reserve: Reserve,
    race_window_s: float,
) -> list[str]:
    """Take the stock. Returns the SKUs that could not be reserved."""

    if reserve is Reserve.ATOMIC:
        return await _reserve_atomic(session, lines)

    return await _reserve_read_then_write(session, lines, race_window_s)


async def _release(session: AsyncSession, lines: list[dict]) -> None:
    """Undo a reservation: an increment, safe in both reservation modes."""

    for line in lines:
        await session.execute(
            update(Inventory)
            .where(Inventory.sku == line["sku"])
            .values(available=Inventory.available + line["qty"])
        )


async def _reserve_atomic(session: AsyncSession, lines: list[dict]) -> list[str]:
    short: list[str] = []

    for line in lines:
        result = await session.execute(
            update(Inventory)
            .where(Inventory.sku == line["sku"], Inventory.available >= line["qty"])
            .values(available=Inventory.available - line["qty"])
        )

        if result.rowcount == 0:
            short.append(line["sku"])

    return short


async def _reserve_read_then_write(
    session: AsyncSession, lines: list[dict], race_window_s: float
) -> list[str]:
    short = [ln["sku"] for ln in lines if ln["available_seen"] < ln["qty"]]

    if short:
        return short

    # The window a real handler spends doing other work. Two requests that
    # both got here have both already read the same availability.
    await asyncio.sleep(race_window_s)

    for line in lines:
        await session.execute(
            update(Inventory)
            .where(Inventory.sku == line["sku"])
            .values(available=line["available_seen"] - line["qty"])
        )

    return []


# -------------------------------------------------------------------- payment


async def _charge(
    payments: httpx.AsyncClient, order_id: str, total_cents: int, method: dict
) -> Payment | None:
    """Charge the card at the gateway. None when the gateway cannot answer."""

    try:
        reply = await payments.post(
            CHARGES_PATH,
            json={
                "amount_cents": total_cents,
                "currency": "gbp",
                "source": str(method.get("token", "")),
                "metadata": {"order_id": order_id},
            },
            headers={"idempotency-key": order_id},
        )
    except httpx.HTTPError:
        return None

    if reply.status_code == HTTP_OK:
        return _payment(order_id, total_cents, "authorized", reply.json()["id"], None)

    if reply.status_code == HTTP_PAYMENT_REQUIRED:
        error = reply.json().get("error", {})
        return _payment(order_id, total_cents, "declined", error.get("charge"), error.get("decline_code"))

    return None


def _payment(order_id: str, total: int, state: str, reference: str | None, decline: str | None) -> Payment:
    return Payment(
        id=f"pay_{uuid4().hex[:12]}",
        order_id=order_id,
        provider=PROVIDER,
        state=state,
        amount_cents=total,
        reference=reference,
        decline_code=decline,
    )


def _declined_body(order_id: str, payment: Payment) -> dict:
    return {
        "type": "https://demo.shop/errors/payment-declined",
        "title": "Payment was declined",
        "status": HTTP_PAYMENT_REQUIRED,
        "order_id": order_id,
        "payment": {
            "state": payment.state,
            "provider": payment.provider,
            "decline_code": payment.decline_code,
        },
    }


def _confirmed_body(
    order_id: str,
    lines: list[dict],
    subtotal: int,
    tax: int,
    total: int,
    payment: Payment,
) -> dict:
    return {
        "order_id": order_id,
        "status": "confirmed",
        "currency": "GBP",
        "totals": {
            "subtotal_cents": subtotal,
            "shipping_cents": SHIPPING_CENTS,
            "tax_cents": tax,
            "grand_total_cents": total,
        },
        "payment": {
            "state": payment.state,
            "provider": payment.provider,
            "reference": payment.reference,
        },
        "line_items": [
            {"sku": ln["sku"], "name": ln["name"], "qty": ln["qty"], "unit_cents": ln["unit_cents"]}
            for ln in lines
        ],
    }
