"""Pair-walk an original recording against its replay."""

from infrareplay.plugins.registry import get_registry
from infrareplay.schema import (
    ComparisonCategory,
    ComparisonResult,
    Event,
    Recording,
)


def compare_run(
    original: Recording,
    replayed_events: list[Event],
    links: dict[str, str],
) -> list[ComparisonResult]:
    registry = get_registry()

    replay_by_id = {e.event_id: e for e in replayed_events}
    replay_id = replayed_events[0].recording_id if replayed_events else ""
    consumed: set[str] = set()

    results: list[ComparisonResult] = []

    for src in original.events:
        comparator_cls = registry.comparator_for(src.event_type.value)

        if comparator_cls is None:
            continue  # request / query steps have no standalone comparator

        replay_eid = links.get(src.event_id)
        replay_event = replay_by_id.get(replay_eid) if replay_eid else None

        if replay_event is None:
            results.append(
                ComparisonResult(
                    replay_recording_id=replay_id,
                    original_event_id=src.event_id,
                    event_type=src.event_type.value,
                    category=ComparisonCategory.MISSING,
                )
            )
            continue

        consumed.add(replay_event.event_id)
        results.append(comparator_cls().compare(src, replay_event))

    # Anything replayed that never mapped back to an original.
    for event in replayed_events:
        if event.event_id in consumed:
            continue

        if registry.comparator_for(event.event_type.value) is None:
            continue

        results.append(
            ComparisonResult(
                replay_recording_id=replay_id,
                replayed_event_id=event.event_id,
                event_type=event.event_type.value,
                category=ComparisonCategory.NEW,
            )
        )

    return results
