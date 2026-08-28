"""One call: replay a recording, persist the run, compare, persist results.

Used only by the API layer so the CLI and dashboard stay thin clients.
"""

from infrareplay.comparison import compare_run
from infrareplay.recording import (
    get_recording,
    new_id,
    save_comparison,
    save_replay_run,
)
from infrareplay.replay.engine import ReplayError, SafetyMode, replay_recording
from infrareplay.schema import ComparisonCategory, ReplayRun


async def run_replay(
    *,
    recording_id: str,
    target: str,
    safety: SafetyMode = SafetyMode.SAFE,
    substitutions: dict[str, str] | None = None,
) -> tuple[ReplayRun, dict[str, int]]:
    """Returns the ReplayRun plus a per-category count of the comparison."""

    source = await get_recording(recording_id)

    if source is None:
        raise ReplayError(f"recording {recording_id!r} not found")

    replay_recording_id = new_id("rpl")

    replayed_events, links = replay_recording(
        source,
        target=target,
        replay_recording_id=replay_recording_id,
        safety=safety,
        substitutions=substitutions,
    )

    run = await save_replay_run(
        source_recording_id=recording_id,
        target=target,
        replayed_events=replayed_events,
        links=links,
    )

    results = compare_run(source, replayed_events, links)
    await save_comparison(run.replay_recording_id, results)

    summary = {c.value: 0 for c in ComparisonCategory}

    for r in results:
        summary[r.category.value] += 1

    return run, summary
