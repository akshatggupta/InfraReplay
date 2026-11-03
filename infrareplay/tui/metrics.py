"""Turn a /metrics scrape into the handful of numbers the overview shows."""

from prometheus_client.parser import text_string_to_metric_families

# Overview tiles: (label, sample name, label filter). Order is display order.
TILES = (
    ("recordings", "infrareplay_recordings_total", {"status": "completed"}),
    ("events", "infrareplay_events_captured_total", {}),
    ("replays", "infrareplay_replay_total", {}),
    ("failures", "infrareplay_replay_failures_total", {}),
    ("live captures", "infrareplay_active_captures", {}),
    ("refused", "infrareplay_auth_denied_total", {}),
    ("dropped", "infrareplay_events_dropped_total", {}),
)


def parse(text: str) -> list[tuple[str, dict, float]]:
    return [
        (sample.name, sample.labels, sample.value)
        for family in text_string_to_metric_families(text)
        for sample in family.samples
    ]


def total(samples: list[tuple[str, dict, float]], name: str, **where) -> float:
    """Sum every series of `name` whose labels include `where`."""

    return sum(
        value
        for sample, labels, value in samples
        if sample == name and all(labels.get(k) == v for k, v in where.items())
    )


def by_label(samples: list[tuple[str, dict, float]], name: str, label: str) -> dict[str, float]:
    out: dict[str, float] = {}

    for sample, labels, value in samples:
        if sample != name or label not in labels:
            continue

        out[labels[label]] = out.get(labels[label], 0.0) + value

    return out
