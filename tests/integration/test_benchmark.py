"""v10: the benchmark harness runs end to end and reports what it measured.

A smoke run — two users for a few seconds — through the real harness: its
own stack of real processes, the Locust driver, a real capture. It checks the
report is produced and complete, not what the numbers are.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_S = 300


def test_quick_benchmark_produces_a_complete_report(tmp_path):
    done = subprocess.run(
        [sys.executable, str(ROOT / "benchmark" / "run.py"), "--quick", "--no-latest", "--out", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )

    assert done.returncode == 0, done.stderr[-2000:]

    summary = json.loads((tmp_path / "summary.json").read_text())
    report = (tmp_path / "report.md").read_text()
    (level,) = summary["levels"]
    capture = level["captured"]["capture"]

    # Measured, not assumed: calibration counted a checkout's events.
    assert summary["events_per_checkout"] > 0

    # Both modes ran real requests, and nothing failed.
    for mode in ("direct", "captured"):
        stats = level[mode]["stats"]["Aggregated"]
        assert stats["requests"] > 0
        assert stats["failures"] == 0

    # Capture kept every event of every request it saw.
    assert capture["completeness_pct"] == 100.0
    assert capture["events_dropped"] == 0

    # The report carries its method, not just numbers.
    assert "## Method" in report
    assert summary["machine"]["packages"]["locust"] != "not installed"
