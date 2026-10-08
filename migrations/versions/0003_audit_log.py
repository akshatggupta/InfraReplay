"""Audit log of capture, replay and bundle actions

Revision ID: 0003_audit_log
Revises: 0002_bigint_clock
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_audit_log"
down_revision = "0002_bigint_clock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("action", sa.String(32), nullable=False, index=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("recording_id", sa.String(64), nullable=True, index=True),
        sa.Column("target", sa.String(512), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
