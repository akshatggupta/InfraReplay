"""Replay a Recording against a target.

Two kinds of target:

  - "mock"              a deterministic function returning the canonical
                        *clean* outcome. Replaying the buggy fixture against
                        it surfaces real MATCH and DIFFERENT rows with no
                        infrastructure running (v1).
  - "http(s)://host"    a live service. Requests are actually sent and the
                        real responses recorded (v4).

Safety lives here, not in the callers: a production-looking target is
refused unless the caller passes SafetyMode.UNSAFE.
"""

import asyncio
from collections.abc import Iterator
from enum import Enum

from infrareplay import config
from infrareplay.plugins.mock_capture.fixtures import Scenario, build
from infrareplay.replay.http_target import HttpTarget
from infrareplay.replay.substitution import AutoMode, SubstitutionEngine
from infrareplay.schema import Event, EventType, Recording

MOCK_TARGET = "mock"

_HTTP_SCHEMES = ("http://", "https://")


class SafetyMode(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"


class ReplayError(RuntimeError):
    pass


def _looks_like_prod(target: str) -> bool:
    lowered = target.lower()

    return any(marker in lowered for marker in config.PROD_TARGET_MARKERS)


def ensure_safe(target: str, safety: SafetyMode) -> None:
    """Raise unless `target` may be replayed against.

    Callers check this *before* opening a replay recording, so a refused
    replay leaves nothing behind; the engine checks it again below, because
    safety is the engine's invariant, not the caller's good manners.
    """

    if safety is SafetyMode.SAFE and _looks_like_prod(target):
        raise ReplayError(
            f"target {target!r} looks like production; pass unsafe to override"
        )


async def replay_recording(
    source: Recording,
    *,
    target: str,
    replay_recording_id: str,
    safety: SafetyMode = SafetyMode.SAFE,
    substitutions: dict[str, str] | None = None,
    auto: AutoMode = AutoMode.OFF,
    concurrency: int = 1,
) -> list[Event]:
    """Return the events produced by replaying `source` against `target`.

    `concurrency` > 1 sends that many requests at once — the only way to
    reproduce a race, which is exactly what the demo shop's bug is.
    """

    ensure_safe(target, safety)

    if not source.events:
        raise ReplayError("source recording has no events to replay")

    subs = SubstitutionEngine(substitutions, auto=auto)

    if target == MOCK_TARGET:
        return _replay_mock(source, replay_recording_id, subs)

    if target.startswith(_HTTP_SCHEMES):
        return await _replay_http(source, replay_recording_id, target, subs, concurrency)

    raise ReplayError(f"unsupported replay target {target!r}")


# ----------------------------------------------------------------------- mock


def _replay_mock(
    source: Recording,
    replay_recording_id: str,
    subs: SubstitutionEngine,
) -> list[Event]:
    canonical = build(replay_recording_id, Scenario.CLEAN)
    by_sequence = {e.sequence: e for e in canonical}

    replayed: list[Event] = []

    for original in source.events:
        stand_in = by_sequence.get(original.sequence)

        if stand_in is None:
            continue  # nothing in the canonical stream matches this step

        payload = (
            subs.apply(original.payload)
            if original.event_type is EventType.HTTP_REQUEST
            else stand_in.payload
        )

        replayed.append(
            stand_in.model_copy(
                update={
                    "payload": payload,
                    "correlation_id": canonical[0].correlation_id,
                }
            )
        )

    return replayed


# ----------------------------------------------------------------------- http


async def _replay_http(
    source: Recording,
    replay_recording_id: str,
    target: str,
    subs: SubstitutionEngine,
    concurrency: int,
) -> list[Event]:
    requests = [e for e in source.events if e.event_type is EventType.HTTP_REQUEST]

    if not requests:
        raise ReplayError("recording contains no HTTP requests to replay")

    payloads = [subs.apply(r.payload) for r in requests]
    replayed: list[Event] = []

    async with HttpTarget(target, recording_id=replay_recording_id) as live:
        for batch in _batches(payloads, max(concurrency, 1)):
            for pair in await asyncio.gather(*(live.send(p) for p in batch)):
                replayed.extend(pair)

    return replayed


def _batches(payloads: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(payloads), size):
        yield payloads[start : start + size]
