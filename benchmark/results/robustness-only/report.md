# InfraReplay capture-overhead benchmark

Run 2026-11-02T19:14:12+00:00 on 12th Gen Intel(R) Core(TM) i5-12500H (16 logical cores, 7.6 GB RAM). Databases: API sqlite, shop sqlite.

## Cost of capture, per load level

| Load | Users | Throughput | Checkout p50 | p95 | p99 | Events captured | Completeness | Stored | Finalise |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | 5 | -89.4% | +1,838.0 ms | +4,430.0 ms | +5,600.0 ms | 976 | 100.0% | 19 KB/s | 1.91 s |
| medium | 20 | -94.5% | +6,405.0 ms | +9,500.0 ms | +8,900.0 ms | 1,300 | 103.8% | 25 KB/s | 3.7 s |
| heavy | 50 | -93.8% | +17,320.0 ms | +25,800.0 ms | +27,800.0 ms | 1,284 | 106.6% | 25 KB/s | 4.25 s |

Throughput is the change in requests per second when capture is on; the latency
columns are milliseconds added to the checkout at each percentile. Completeness is
events captured against events expected from the requests Locust completed.

## Raw results

| Load | Mode | Requests | Failures | Req/s | Checkout p50 | p95 | p99 | Shop CPU | API CPU | Shop RSS | API RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| light | direct | 744 | 0 | 21.8 | 62.0 ms | 270.0 ms | 800.0 ms | 18.1% | 0.3% | 89.3 MB | 87.6 MB |
| light | captured | 79 | 0 | 2.3 | 1,900.0 ms | 4,700.0 ms | 6,400.0 ms | 13.4% | 15.3% | 96.0 MB | 105.9 MB |
| medium | direct | 1,705 | 0 | 50.31 | 95.0 ms | 1,500.0 ms | 4,100.0 ms | 39.5% | 0.2% | 101.7 MB | 112.1 MB |
| medium | captured | 97 | 0 | 2.79 | 6,500.0 ms | 11,000.0 ms | 13,000.0 ms | 16.0% | 20.3% | 110.8 MB | 135.5 MB |
| heavy | direct | 1,563 | 0 | 44.65 | 680.0 ms | 3,200.0 ms | 6,200.0 ms | 40.7% | 0.3% | 111.7 MB | 136.9 MB |
| heavy | captured | 94 | 0 | 2.78 | 18,000.0 ms | 29,000.0 ms | 34,000.0 ms | 16.7% | 22.1% | 122.9 MB | 176.1 MB |

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
