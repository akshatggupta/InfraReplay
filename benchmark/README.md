# Benchmark — what capture costs

```bash
benchmark/run.sh            # light, medium and heavy load, direct and captured (~6 min)
benchmark/run.sh --quick    # a smoke run, seconds
```

The latest report is in [`results/latest/report.md`](results/latest/report.md),
with every number behind it in `results/latest/summary.json`.

## What it measures

Each load level runs the same workload twice against a fresh, isolated stack
(API, demo shop and payment gateway as real processes on free ports, with
throwaway databases):

```
direct     shopper ─────────────────────────────> demo shop ──> payment gateway
captured   shopper ──> capture proxy ──> demo shop ──> payment gateway
                                             └──(its SQL and outbound calls)──> API
```

For every run it records requests, failures, requests per second, checkout
latency at p50/p95/p99, and the mean CPU and peak memory of each process.
For captured runs it also records how many events were captured, how many were
**expected** from the requests that completed (the completeness check — capture
that drops data under load shows up here), the bytes stored per second, and how
long finalising the recording took.

## How

- **Workload** — `locustfile.py`: three checkouts per inventory browse, 50–200 ms
  think time. Light 5 users, medium 20, heavy 50; 30 s each after a warm-up.
- **Timing** — `drive.py` runs Locust as a library and divides by the wall-clock
  window it measured itself. Locust's `--run-time` flag is not used: its timer is
  scheduled on gevent's loop clock, which goes stale while Locust's imports block,
  and on a slow filesystem a "10 s" run measured 4 s of load.
- **Expected events** — counted by a calibration capture of one checkout and one
  browse before the runs, not hard-coded, so the check stays right when the
  shop's SQL changes.
- **Isolation** — stock is reset before every run; the demo's deliberate race
  window is set to 0 and inventory locking is on, so the benchmark measures the
  shop, not the bug it exists to demonstrate.
- **Provenance** — CPU model, cores, RAM, OS, Python and package versions are
  written into every report.

## Limits

- Everything shares one machine, so absolute numbers understate dedicated hosts.
  The direct-versus-captured comparison is like for like.
- One uvicorn worker per service.
- SQLite by default. `--api-db` and `--shop-db` take any SQLAlchemy URL, e.g. the
  Compose Postgres, to measure another backend.

No performance figure appears anywhere in this project unless a report here
backs it.
