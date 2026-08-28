"""The Event model — the atomic unit of every recording."""

from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """The four event kinds v1 understands.

    HTTP:  request  -> response
    PG:    query    -> result
    """

    HTTP_REQUEST = "http.request"
    HTTP_RESPONSE = "http.response"
    PG_QUERY = "postgres.query"
    PG_RESULT = "postgres.result"


class Event(BaseModel):
    """One captured or replayed step.

    `payload` is intentionally an open dict: each event_type documents its
    own shape (see the mock capture fixtures for canonical examples) so the
    schema stays stable while payloads evolve.
    """

    schema_version: int = 1

    recording_id: str
    event_id: str
    parent_event_id: str | None = None

    # Ordering within a recording. Monotonic, gap-free per recording.
    sequence: int

    timestamp_ns: int
    duration_ns: int

    event_type: EventType
    service: str

    # Ties an HTTP request to the DB events it triggered.
    correlation_id: str

    # Reused from OpenTelemetry context when the caller already has it.
    trace_id: str | None = None
    span_id: str | None = None

    payload: dict = Field(default_factory=dict)
