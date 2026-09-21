"""ORM rows. Kept deliberately close to the Pydantic schema."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, DateTime


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecordingRow(Base):
    __tablename__ = "recordings"

    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str] = mapped_column(String(128), default="default")
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256), default="")
    capture_plugin: Mapped[str] = mapped_column(String(64), default="")

    source_recording_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Locator of the full recording snapshot in blob storage.
    blob_locator: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recording_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("recordings.recording_id"), index=True
    )
    parent_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # For replay events: the original event this one reproduces.
    source_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    sequence: Mapped[int] = mapped_column(Integer)
    # Nanosecond wall clock overflows a 4-byte int on Postgres.
    timestamp_ns: Mapped[int] = mapped_column(BigInteger)
    duration_ns: Mapped[int] = mapped_column(BigInteger)

    event_type: Mapped[str] = mapped_column(String(32))
    service: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ComparisonRow(Base):
    __tablename__ = "comparison_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    replay_recording_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("recordings.recording_id"), index=True
    )
    original_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    replayed_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    event_type: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(16))
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
