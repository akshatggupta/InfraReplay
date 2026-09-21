"""One call: replay a recording, persist the run, compare, persist results.

Used only by the API layer so the CLI and dashboard stay thin clients.

The replay recording row exists *before* the first request is sent, so an
instrumented target can stream its own SQL events into the run while it is
still in flight (see `infrareplay.agent`).
"""

from infrareplay.comparison import compare_run
from infrareplay.recording import (
    append_events,
    fail_recording,
    finish_recording,
    get_recording,
    link_replay_events,
    new_id,
    save_comparison,
    start_recording,
)
from infrareplay.replay.engine import (
    ReplayError,
    SafetyMode,
    ensure_safe,
    replay_recording,
)
from infrareplay.replay.linking import link_streams
from infrareplay.replay.substitution import AutoMode
from infrareplay.schema import ComparisonCategory, RecordingKind, ReplayRun


async def run_replay(
    *,
    recording_id: str,
    target: str,
    safety: SafetyMode = SafetyMode.SAFE,
    substitutions: dict[str, str] | None = None,
    auto: AutoMode = AutoMode.OFF,
    concurrency: int = 1,
) -> tuple[ReplayRun, dict[str, int]]:
    """Returns the ReplayRun plus a per-category count of the comparison."""

    ensure_safe(target, safety)

    source = await get_recording(recording_id)

    if source is None:
        raise ReplayError(f"recording {recording_id!r} not found")

    replay_id = new_id("rpl")

    await start_recording(
        recording_id=replay_id,
        project=source.project,
        kind=RecordingKind.REPLAY,
        title=f"Replay of {source.title or recording_id}",
        target=target,
        source_recording_id=recording_id,
    )

    try:
        replayed = await replay_recording(
            source,
            target=target,
            replay_recording_id=replay_id,
            safety=safety,
            substitutions=substitutions,
            auto=auto,
            concurrency=concurrency,
        )
    except ReplayError as exc:
        await fail_recording(replay_id, str(exc))
        raise

    await append_events(replay_id, replayed)

    # Re-read: an instrumented target may have added its own events.
    run_recording = await finish_recording(replay_id)

    links = link_streams(source.events, run_recording.events)
    await link_replay_events(replay_id, links)

    results = compare_run(source, run_recording.events, links)
    await save_comparison(replay_id, results)

    summary = {c.value: 0 for c in ComparisonCategory}

    for result in results:
        summary[result.category.value] += 1

    run = ReplayRun(
        replay_recording_id=replay_id,
        source_recording_id=recording_id,
        target=target,
        event_links=links,
    )

    return run, summary
