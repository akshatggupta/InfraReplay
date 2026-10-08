"""v9 DoD: /metrics is scrapeable, the dashboard matches it, logs are JSON.

The Prometheus registry is process-wide and outlives each test, so every
counter assertion is a before/after delta, never an absolute.
"""

import io
import json
import re
from pathlib import Path

import httpx
import pytest
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families

from infrareplay import config
from infrareplay.api import create_app
from infrareplay.observability import configure

_DASHBOARD = Path(__file__).parents[2] / "docs/grafana/infrareplay-dashboard.json"

# The spec's list, under the names Prometheus conventions give them.
_SPEC_SAMPLES = {
    "infrareplay_recordings_total",
    "infrareplay_events_captured_total",
    "infrareplay_capture_errors_total",
    "infrareplay_replay_total",
    "infrareplay_replay_failures_total",
    "infrareplay_storage_bytes_total",
}

_SECRET = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJsb2cifQ.LOGSECRETsig"


@pytest.fixture
async def api():
    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


def _value(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _sample_names(exposition: str) -> set[str]:
    return {
        sample.name
        for family in text_string_to_metric_families(exposition)
        for sample in family.samples
    }


async def test_metrics_endpoint_is_scrapeable(api: httpx.AsyncClient):
    await api.post("/api/demo/seed")  # so the labelled series exist

    response = await api.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    # Parses as Prometheus exposition format and carries the spec's metrics.
    assert _SPEC_SAMPLES <= _sample_names(response.text)


async def test_counters_follow_the_work(api: httpx.AsyncClient):
    before = {
        "captures": _value("infrareplay_recordings_total", kind="capture", status="completed"),
        "replays": _value("infrareplay_replay_total", target_kind="mock"),
        "refused": _value("infrareplay_replay_failures_total", reason="refused"),
        "different": _value("infrareplay_comparison_results_total", category="DIFFERENT"),
        "responses": _value("infrareplay_events_captured_total", event_type="http.response"),
        "bytes": _value("infrareplay_storage_bytes_total"),
    }

    seeded = (await api.post("/api/demo/seed")).json()
    buggy = next(r for r in seeded if "fails" in r["title"])

    await api.post("/api/replays", json={"recording_id": buggy["recording_id"], "target": "mock"})
    await api.post(
        "/api/replays",
        json={"recording_id": buggy["recording_id"], "target": "https://api.production.example.com"},
    )

    assert _value("infrareplay_recordings_total", kind="capture", status="completed") - before["captures"] == 2
    assert _value("infrareplay_replay_total", target_kind="mock") - before["replays"] == 1
    assert _value("infrareplay_replay_failures_total", reason="refused") - before["refused"] == 1
    assert _value("infrareplay_comparison_results_total", category="DIFFERENT") - before["different"] == 2
    assert _value("infrareplay_events_captured_total", event_type="http.response") - before["responses"] >= 3
    assert _value("infrareplay_storage_bytes_total") > before["bytes"]


async def test_metrics_stay_public_when_keys_are_on(api: httpx.AsyncClient, monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", {"s3cret": "ci"})

    assert (await api.get("/api/recordings")).status_code == 401
    assert (await api.get("/metrics")).status_code == 200


async def test_dashboard_only_queries_metrics_the_api_exports(api: httpx.AsyncClient):
    await api.post("/api/demo/seed")
    exported = _sample_names((await api.get("/metrics")).text)

    dashboard = json.loads(_DASHBOARD.read_text())
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    queried = {name for e in exprs for name in re.findall(r"infrareplay_[a-z_]+", e)}

    # Histograms are queried by their _bucket/_count series; gauges and
    # counters by name. Either way the series must be one the API exports.
    unknown = {
        name for name in queried
        if name not in exported and f"{name}_bucket" not in exported
    }

    assert queried, "dashboard has no InfraReplay queries"
    assert unknown == set()


@pytest.fixture
def captured_logs():
    buffer = io.StringIO()
    configure(stream=buffer, force=True)
    yield buffer
    configure(force=True)


async def test_logs_are_json_and_never_carry_secrets(api: httpx.AsyncClient, captured_logs):
    response = await api.get(
        "/api/recordings",
        headers={"authorization": _SECRET, "x-request-id": "req-trace-42"},
    )

    lines = [json.loads(line) for line in captured_logs.getvalue().splitlines() if line]
    request_line = next(l for l in lines if l["event"] == "http.request")

    assert response.headers["x-request-id"] == "req-trace-42"
    assert request_line["request_id"] == "req-trace-42"
    assert request_line["route"] == "/api/recordings"
    assert {"level", "timestamp"} <= set(request_line)
    assert "LOGSECRET" not in captured_logs.getvalue()
