"""Run the Locust workload for an exact wall-clock window; print stats as JSON.

    python benchmark/drive.py --host http://127.0.0.1:3000 --users 20 --seconds 30

Why not `locust --run-time`: its timer is scheduled on gevent's loop clock,
which goes stale while Locust's own imports block. On a slow filesystem that
fired the timer seconds early — a "10 s" run measured 4 s of load. Here the
loop clock is refreshed before the run starts, and every rate is divided by
seconds measured with a monotonic clock, not by the seconds asked for.
"""

import locust  # noqa: F401 — first: Locust patches the socket stack for gevent

import argparse
import json
import sys
import time
from pathlib import Path

import gevent
from locust import events
from locust.env import Environment

sys.path.insert(0, str(Path(__file__).resolve().parent))

from locustfile import Shopper  # noqa: E402

_PERCENTILES = {"p50_ms": 0.50, "p95_ms": 0.95, "p99_ms": 0.99}


def _row(entry, elapsed: float) -> dict:
    row = {
        "requests": entry.num_requests,
        "failures": entry.num_failures,
        "rps": round(entry.num_requests / elapsed, 2) if elapsed else 0.0,
        "avg_ms": round(entry.avg_response_time, 1),
    }

    for key, pct in _PERCENTILES.items():
        row[key] = float(entry.get_response_time_percentile(pct)) if entry.num_requests else None

    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--users", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()

    env = Environment(user_classes=[Shopper], events=events, host=args.host)
    runner = env.create_local_runner()

    # Bring the loop clock up to date after the slow imports above.
    gevent.get_hub().loop.update_now()
    gevent.sleep(0)

    started = time.monotonic()
    runner.start(args.users, spawn_rate=args.users)

    while time.monotonic() - started < args.seconds:
        gevent.sleep(0.1)

    runner.quit()
    elapsed = time.monotonic() - started

    rows = {"Aggregated": _row(env.stats.total, elapsed)}

    for (name, _method), entry in env.stats.entries.items():
        rows[name] = _row(entry, elapsed)

    json.dump({"elapsed_s": round(elapsed, 2), "rows": rows}, sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
