"""Prometheus metrics, scraped from GET /metrics.

The spec's list, prefixed `infrareplay_` and named the Prometheus way
(counters end in `_total`). Labels are kept to small fixed sets — never a
recording id or a URL — because every distinct label value is a new time
series, and an unbounded label is how a metrics server falls over.

Every known label combination is created at zero on import. A labelled
series otherwise does not exist until its first increment, so "replay
failures" would read *no data* rather than 0 until the first one ever
happened — a gap on the dashboard and a trap for absent-series alerts.

This module only defines the instruments; the layers that do the work
update them, and nothing here imports any of those layers.
"""

from enum import Enum

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from infrareplay.schema import ComparisonCategory, EventType, RecordingKind, RecordingStatus


class TargetKind(str, Enum):
    MOCK = "mock"
    HTTP = "http"


class ReplayFailure(str, Enum):
    REFUSED = "refused"
    NOT_FOUND = "not_found"
    ERROR = "error"


_FINAL_STATUSES = (RecordingStatus.COMPLETED, RecordingStatus.FAILED)

RECORDINGS = Counter(
    "infrareplay_recordings_total",
    "Recordings finalised, by kind (capture | replay) and status (completed | failed).",
    ["kind", "status"],
)

EVENTS_CAPTURED = Counter(
    "infrareplay_events_captured_total",
    "Events stored, by event type.",
    ["event_type"],
)

CAPTURE_ERRORS = Counter(
    "infrareplay_capture_errors_total",
    "Captures that ended in failure.",
)

EVENTS_DROPPED = Counter(
    "infrareplay_events_dropped_total",
    "Captured events that could not be stored after retries. Should stay 0.",
)

ACTIVE_CAPTURES = Gauge(
    "infrareplay_active_captures",
    "Live capture sessions right now.",
)

REPLAYS = Counter(
    "infrareplay_replay_total",
    "Replays started, by target kind (mock | http).",
    ["target_kind"],
)

REPLAY_FAILURES = Counter(
    "infrareplay_replay_failures_total",
    "Replays that did not complete, by reason (refused | not_found | error).",
    ["reason"],
)

REPLAY_SECONDS = Histogram(
    "infrareplay_replay_duration_seconds",
    "Wall time of a replay, including its comparison.",
    ["target_kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

COMPARISON_RESULTS = Counter(
    "infrareplay_comparison_results_total",
    "Comparison rows produced, by category.",
    ["category"],
)

STORAGE_BYTES = Counter(
    "infrareplay_storage_bytes_total",
    "Snapshot bytes handed to blob storage (before encryption, when it is on).",
)

AUTH_DENIED = Counter(
    "infrareplay_auth_denied_total",
    "API calls refused for a missing or invalid key.",
)

HTTP_SECONDS = Histogram(
    "infrareplay_http_request_duration_seconds",
    "API latency, by method, route template and status code.",
    ["method", "route", "status"],
)


def target_kind(target: str) -> str:
    """Collapse a replay target to a bounded label: never the URL itself."""

    kind = TargetKind.MOCK if target == TargetKind.MOCK.value else TargetKind.HTTP

    return kind.value


def _zero_every_series() -> None:
    for kind in RecordingKind:
        for status in _FINAL_STATUSES:
            RECORDINGS.labels(kind.value, status.value)

    for event_type in EventType:
        EVENTS_CAPTURED.labels(event_type.value)

    for target in TargetKind:
        REPLAYS.labels(target.value)
        REPLAY_SECONDS.labels(target.value)

    for reason in ReplayFailure:
        REPLAY_FAILURES.labels(reason.value)

    for category in ComparisonCategory:
        COMPARISON_RESULTS.labels(category.value)


_zero_every_series()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
