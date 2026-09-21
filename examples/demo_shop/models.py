"""Schema and seed data for the demo shop."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))

    # Stand-in for a session cookie; deliberately not a secret header, so the
    # demo still replays after redaction masks Authorization.
    handle: Mapped[str] = mapped_column(String(64), index=True)


class Inventory(Base):
    __tablename__ = "inventory"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    unit_cents: Mapped[int] = mapped_column(Integer)
    available: Mapped[int] = mapped_column(Integer)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16))
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"))
    sku: Mapped[str] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer)
    unit_cents: Mapped[int] = mapped_column(Integer)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"))
    provider: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decline_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


SEED_USER = User(id="usr_4a1c8e", email="dana.lang@example.com", name="Dana Lang", handle="dana")

SEED_STOCK = 1

SEED_INVENTORY = (
    ("AEROPRESS-GO", "AeroPress Go", 3999),
    ("FILTER-350", "Micro-filters (350 pack)", 899),
)
