"""Pydantic schema: the real API every component agrees on."""

from infrareplay.schema.comparison import ComparisonCategory, ComparisonResult
from infrareplay.schema.event import Event, EventType
from infrareplay.schema.recording import (
    Recording,
    RecordingKind,
    RecordingStatus,
    ReplayRun,
)

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "Event",
    "EventType",
    "Recording",
    "RecordingKind",
    "RecordingStatus",
    "ReplayRun",
    "ComparisonCategory",
    "ComparisonResult",
]
