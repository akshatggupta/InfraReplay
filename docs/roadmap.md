# Where the build is

| Version | Scope | State |
|---|---|---|
| v1 | Full pipeline on synthetic data | done |
| v2 | Real HTTP capture plugin | done |
| v3 | Real SQL capture + correlation | done |
| v4 | Real replay against live targets | done |
| v5 | Comparison hardening | done |
| v6 | Demo shop + intentional bug | done |
| v7 | CLI completeness, export/import | done |
| v8 | Security hardening (encryption at rest, API keys, audit log) | not started |
| v9 | Observability (Prometheus metrics, structlog JSON) | not started |
| v10 | Benchmark suite (Locust, published methodology) | not started |
| v11 | External service mocking on replay | not started |
| v12 | A third-party protocol plugin as a separate package | not started |

Seven of twelve versions — the whole capture → correlate → store → replay →
compare loop, on real traffic, with a real bug caught end to end. What is
left is hardening (v8–v11) and one more proof of the plugin system (v12);
none of it changes the pipeline above.

Each version's own note: `docs/v1.md` … `docs/v7.md`.
