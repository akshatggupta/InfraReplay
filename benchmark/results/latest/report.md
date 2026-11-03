# InfraReplay capture-overhead benchmark

Run 2026-11-02T19:49:58+00:00 on 12th Gen Intel(R) Core(TM) i5-12500H (16 logical cores, 7.6 GB RAM). Databases: API sqlite, shop sqlite.

## Cost of capture, per load level

| Load | Users | Throughput | Checkout p50 | p95 | p99 | Events captured | Complete requests | Dropped | Stored | Finalise |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | 5 | -12.3% | +33.0 ms | +40.0 ms | -30.0 ms | 10,188 | 100.0% | 0 | 217 KB/s | 17.05 s |
| medium | 20 | -24.0% | +150.0 ms | +0.0 ms | +2,400.0 ms | 10,540 | 100.0% | 0 | 224 KB/s | 15.89 s |
| heavy | 50 | -45.7% | +950.0 ms | +1,800.0 ms | +3,100.0 ms | 12,476 | 100.0% | 0 | 260 KB/s | 27.39 s |

Throughput is the change in requests per second when capture is on; the latency
columns are milliseconds added to the checkout at each percentile. Complete requests
is the share of captured requests that kept every event they should have; Dropped is
the API's own count of events it failed to store.

## Raw results

| Load | Mode | Requests | Failures | Req/s | Checkout p50 | p95 | p99 | Shop CPU | API CPU | Shop RSS | API RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | direct | 845 | 0 | 24.85 | 50.0 ms | 140.0 ms | 270.0 ms | 25.3% | 0.3% | 89.5 MB | 86.8 MB |
| light | captured | 720 | 0 | 21.79 | 83.0 ms | 180.0 ms | 240.0 ms | 22.8% | 26.4% | 90.2 MB | 90.5 MB |
| medium | direct | 1,002 | 0 | 29.37 | 180.0 ms | 3,700.0 ms | 5,300.0 ms | 43.0% | 0.3% | 96.3 MB | 114.7 MB |
| medium | captured | 738 | 0 | 22.31 | 330.0 ms | 3,700.0 ms | 7,700.0 ms | 40.6% | 42.1% | 96.0 MB | 117.0 MB |
| heavy | direct | 1,557 | 0 | 46.81 | 650.0 ms | 3,400.0 ms | 5,000.0 ms | 29.6% | 0.2% | 99.2 MB | 124.3 MB |
| heavy | captured | 858 | 0 | 25.4 | 1,600.0 ms | 5,200.0 ms | 8,100.0 ms | 43.3% | 46.6% | 100.3 MB | 125.4 MB |

## Method

- Workload: `benchmark/locustfile.py` — three checkouts per inventory browse, think time 50–200 ms, users all spawned in the first second.
- Locust is driven as a library (`benchmark/drive.py`), and every rate is requests divided by the wall-clock window it measured. Its `--run-time` flag is not used: on a slow filesystem its timer fired seconds early.
- Each level runs 30 s direct, then the same 30 s captured, after a 5 s warm-up. Stock is reset before every run so no checkout fails for lack of it.
- Every checkout makes a real HTTP call to the payment-gateway service, in both modes. Captured runs go through the real capture proxy, and the shop's agent streams its SQL and that outbound call to the API before each response leaves — the production path.
- One checkout produces 18 events and one browse 4, measured by a calibration capture before the runs.
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
