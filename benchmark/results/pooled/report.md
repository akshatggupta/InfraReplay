# InfraReplay capture-overhead benchmark

Run 2026-11-02T19:25:46+00:00 on 12th Gen Intel(R) Core(TM) i5-12500H (16 logical cores, 7.6 GB RAM). Databases: API sqlite, shop sqlite.

## Cost of capture, per load level

| Load | Users | Throughput | Checkout p50 | p95 | p99 | Events captured | Complete requests | Dropped | Stored | Finalise |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | 5 | -25.0% | +65.0 ms | +40.0 ms | -80.0 ms | 8,628 | 100.0% | 0 | 162 KB/s | 21.45 s |
| medium | 20 | -63.9% | +414.0 ms | +2,500.0 ms | +4,300.0 ms | 8,060 | 100.0% | 0 | 161 KB/s | 18.07 s |
| heavy | 50 | -57.2% | +930.0 ms | +2,400.0 ms | +2,800.0 ms | 11,664 | 100.0% | 0 | 226 KB/s | 23.15 s |

Throughput is the change in requests per second when capture is on; the latency
columns are milliseconds added to the checkout at each percentile. Complete requests
is the share of captured requests that kept every event they should have; Dropped is
the API's own count of events it failed to store.

## Raw results

| Load | Mode | Requests | Failures | Req/s | Checkout p50 | p95 | p99 | Shop CPU | API CPU | Shop RSS | API RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | direct | 861 | 0 | 25.05 | 35.0 ms | 170.0 ms | 400.0 ms | 19.6% | 0.3% | 88.9 MB | 88.8 MB |
| light | captured | 657 | 0 | 18.78 | 100.0 ms | 210.0 ms | 320.0 ms | 25.3% | 32.7% | 89.6 MB | 90.6 MB |
| medium | direct | 1,778 | 0 | 52.57 | 96.0 ms | 1,500.0 ms | 3,600.0 ms | 39.4% | 0.2% | 93.9 MB | 112.2 MB |
| medium | captured | 628 | 0 | 18.99 | 510.0 ms | 4,000.0 ms | 7,900.0 ms | 24.9% | 29.6% | 94.0 MB | 115.0 MB |
| heavy | direct | 2,089 | 0 | 60.99 | 470.0 ms | 2,400.0 ms | 4,700.0 ms | 39.3% | 0.2% | 95.3 MB | 123.8 MB |
| heavy | captured | 885 | 0 | 26.08 | 1,400.0 ms | 4,800.0 ms | 7,500.0 ms | 37.0% | 43.5% | 94.1 MB | 126.8 MB |

## Method

- Workload: `benchmark/locustfile.py` — three checkouts per inventory browse, think time 50–200 ms, users all spawned in the first second.
- Locust is driven as a library (`benchmark/drive.py`), and every rate is requests divided by the wall-clock window it measured. Its `--run-time` flag is not used: on a slow filesystem its timer fired seconds early.
- Each level runs 30 s direct, then the same 30 s captured, after a 5 s warm-up. Stock is reset before every run so no checkout fails for lack of it.
- Captured runs go through the real capture proxy, and the shop's agent streams its SQL to the API before each response leaves — exactly the production path.
- One checkout produces 16 events and one browse 4, measured by a calibration capture before the runs.
- The shop's demonstration race window is set to 0 and inventory locking is on: the benchmark measures the shop, not the deliberate bug.
- CPU is the mean of half-second samples of each process (100% = one core); memory is peak resident set size.
- Software: Python 3.12.3, locust 2.47.0, uvicorn 0.52.4, fastapi 0.141.1, starlette 1.6.0, sqlalchemy 2.0.52, aiosqlite 0.22.1, httpx 0.28.1. OS: Linux-5.15.167.4-microsoft-standard-WSL2-x86_64-with-glibc2.39.

## Limits

- Load generator, API, shop and databases share one machine, so they compete for CPU; absolute numbers are lower than on separate hosts. The direct-versus-captured comparison is like for like.
- Each service runs one uvicorn worker.
- SQLite serialises writes; under Postgres the absolute throughput differs. Run with `--api-db` and `--shop-db` to measure another backend.

## Reproduce

```bash
benchmark/run.sh            # this report
benchmark/run.sh --quick    # a smoke run
```
