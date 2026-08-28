"""Initial metadata schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recordings",
        sa.Column("recording_id", sa.String(64), primary_key=True),
        sa.Column("project", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("capture_plugin", sa.String(64), nullable=False),
        sa.Column("source_recording_id", sa.String(64), nullable=True),
        sa.Column("target", sa.String(512), nullable=True),
        sa.Column("blob_locator", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column(
            "recording_id",
            sa.String(64),
            sa.ForeignKey("recordings.recording_id"),
            index=True,
            nullable=False,
        ),
        sa.Column("parent_event_id", sa.String(64), nullable=True),
        sa.Column("source_event_id", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("timestamp_ns", sa.Integer(), nullable=False),
        sa.Column("duration_ns", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(64), index=True, nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )

    op.create_table(
        "comparison_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "replay_recording_id",
            sa.String(64),
            sa.ForeignKey("recordings.recording_id"),
            index=True,
            nullable=False,
        ),
        sa.Column("original_event_id", sa.String(64), nullable=True),
        sa.Column("replayed_event_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("comparison_results")
    op.drop_table("events")
    op.drop_table("recordings")
