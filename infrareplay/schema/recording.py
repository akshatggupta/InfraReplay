"""Recording and ReplayRun — a ReplayRun is just a Recording with a source."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from infrareplay.schema.event import Event


class RecordingKind(str, Enum):
    CAPTURE = "capture"
    REPLAY = "replay"


class RecordingStatus(str, Enum):
    CREATING = "creating"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Recording(BaseModel):
    schema_version: int = 1

    recording_id: str
    project: str = "default"
    kind: RecordingKind = RecordingKind.CAPTURE
    status: RecordingStatus = RecordingStatus.CREATING

    title: str = ""
    capture_plugin: str = ""

    # Set only when kind == REPLAY.
    source_recording_id: str | None = None
    target: str | None = None

    created_at: datetime = Field(default_factory=_now)

    events: list[Event] = Field(default_factory=list)


class ReplayRun(BaseModel):
    """Thin view over the replay Recording plus its per-event linkage."""

    replay_recording_id: str
    source_recording_id: str
    target: str
    created_at: datetime = Field(default_factory=_now)

    # original event_id -> replayed event_id
    event_links: dict[str, str] = Field(default_factory=dict)
