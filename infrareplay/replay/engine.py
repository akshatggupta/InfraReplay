"""Replay a Recording against a target, producing a linked ReplayRun.

v1 targets:
  - "mock": the target is a deterministic function that always yields the
    canonical *clean* outcome. Replaying a buggy recording against it
    therefore surfaces real MATCH and DIFFERENT rows downstream.

Real HTTP targets arrive in v4; the engine boundary here does not change.
"""

from enum import Enum

from infrareplay import config
from infrareplay.plugins.mock_capture.fixtures import Scenario, build
from infrareplay.replay.substitution import SubstitutionEngine
from infrareplay.schema import Event, Recording

MOCK_TARGET = "mock"


class SafetyMode(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"


class ReplayError(RuntimeError):
    pass


def _looks_like_prod(target: str) -> bool:
    lowered = target.lower()

    return any(marker in lowered for marker in config.PROD_TARGET_MARKERS)


def replay_recording(
    source: Recording,
    *,
    target: str,
    replay_recording_id: str,
    safety: SafetyMode = SafetyMode.SAFE,
    substitutions: dict[str, str] | None = None,
) -> tuple[list[Event], dict[str, str]]:
    """Return (replayed_events, links) where links maps original -> replay id."""

    if safety is SafetyMode.SAFE and _looks_like_prod(target):
        raise ReplayError(
            f"target {target!r} looks like production; pass unsafe to override"
        )

    if not source.events:
        raise ReplayError("source recording has no events to replay")

    if target != MOCK_TARGET:
        raise ReplayError(f"v1 replay only supports target {MOCK_TARGET!r}")

    subs = SubstitutionEngine(substitutions)

    # The mock target's response: the canonical clean stream, aligned to the
    # source by `sequence`.
    canonical = build(replay_recording_id, Scenario.CLEAN)
    by_seq = {e.sequence: e for e in canonical}

    replayed: list[Event] = []
    links: dict[str, str] = {}

    for original in source.events:
        target_event = by_seq.get(original.sequence)

        if target_event is None:
            continue  # nothing in the replay stream matches this step

        payload = (
            subs.apply(original.payload)
            if original.event_type.value.startswith("http.request")
            else target_event.payload
        )

        new_event = target_event.model_copy(
            update={
                "payload": payload,
                "correlation_id": canonical[0].correlation_id,
            }
        )

        replayed.append(new_event)
        links[original.event_id] = new_event.event_id

    return replayed, links
