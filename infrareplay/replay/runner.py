"""One call: replay a recording, persist the run, compare, persist results.

Used only by the API layer so the CLI and dashboard stay thin clients.

The replay recording row exists *before* the first request is sent, so an
instrumented target can stream its own SQL events into the run while it is
still in flight (see `infrareplay.agent`).
"""

from time import perf_counter

from infrareplay.capture.context import ExternalsMode
from infrareplay.comparison import compare_run
from infrareplay.observability import get_logger, metrics
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

_log = get_logger(__name__)


async def run_replay(
    *,
    recording_id: str,
    target: str,
    safety: SafetyMode = SafetyMode.SAFE,
    substitutions: dict[str, str] | None = None,
    auto: AutoMode = AutoMode.OFF,
    concurrency: int = 1,
    externals: ExternalsMode = ExternalsMode.RECORDED,
) -> tuple[ReplayRun, dict[str, int]]:
    """Returns the ReplayRun plus a per-category count of the comparison."""

    kind = metrics.target_kind(target)

    try:
        ensure_safe(target, safety)
    except ReplayError:
        metrics.REPLAY_FAILURES.labels(metrics.ReplayFailure.REFUSED.value).inc()
        _log.warning("replay.refused", recording_id=recording_id, target_kind=kind)
        raise

    source = await get_recording(recording_id)

    if source is None:
        metrics.REPLAY_FAILURES.labels(metrics.ReplayFailure.NOT_FOUND.value).inc()
        raise ReplayError(f"recording {recording_id!r} not found")

    metrics.REPLAYS.labels(kind).inc()
    started = perf_counter()

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
            externals=externals,
        )
    except ReplayError as exc:
        metrics.REPLAY_FAILURES.labels(metrics.ReplayFailure.ERROR.value).inc()
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
        metrics.COMPARISON_RESULTS.labels(result.category.value).inc()

    seconds = perf_counter() - started
    metrics.REPLAY_SECONDS.labels(kind).observe(seconds)

    _log.info(
        "replay.finished",
        recording_id=recording_id,
        replay_recording_id=replay_id,
        target_kind=kind,
        concurrency=concurrency,
        seconds=round(seconds, 4),
        **{c.lower(): n for c, n in summary.items()},
    )

    run = ReplayRun(
        replay_recording_id=replay_id,
        source_recording_id=recording_id,
        target=target,
        event_links=links,
    )

    return run, summary
