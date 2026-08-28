"""ComparisonResult — one row per matched event pair."""

from enum import Enum

from pydantic import BaseModel, Field


class ComparisonCategory(str, Enum):
    MATCH = "MATCH"
    DIFFERENT = "DIFFERENT"
    MISSING = "MISSING"  # in original, absent from replay
    NEW = "NEW"  # in replay, absent from original
    ERROR = "ERROR"  # replay raised / could not execute


class ComparisonResult(BaseModel):
    replay_recording_id: str

    # Either side may be None to express MISSING / NEW.
    original_event_id: str | None = None
    replayed_event_id: str | None = None

    event_type: str
    category: ComparisonCategory

    # Free-form per-comparator description of what differs.
    diff: dict = Field(default_factory=dict)
