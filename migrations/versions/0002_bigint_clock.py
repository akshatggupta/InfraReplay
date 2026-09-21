"""Widen the event clock columns to 64-bit

Nanosecond timestamps (~1.8e18) do not fit Postgres' 4-byte INTEGER.

Revision ID: 0002_bigint_clock
Revises: 0001_initial
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_bigint_clock"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_CLOCK_COLUMNS = ("timestamp_ns", "duration_ns")


def upgrade() -> None:
    for column in _CLOCK_COLUMNS:
        op.alter_column("events", column, type_=sa.BigInteger(), existing_nullable=False)


def downgrade() -> None:
    for column in _CLOCK_COLUMNS:
        op.alter_column("events", column, type_=sa.Integer(), existing_nullable=False)
