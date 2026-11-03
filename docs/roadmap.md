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
| v8 | Security hardening (encryption at rest, API keys, audit log) | done |
| v9 | Observability (Prometheus metrics, structlog JSON) | done |
| v10 | Benchmark suite (Locust, published methodology) | done |
| v11 | External service mocking on replay | done |
| v12 | A third-party protocol plugin as a separate package | not started |

Eleven of twelve versions (92%). The capture → correlate → store → replay →
compare loop runs on real traffic, through a real shop that pays through a
real payment-gateway service; a real concurrency bug is caught end to end;
secrets stay out of storage; the system is observable; capture's cost under
load is measured and published (v10); and a replay never charges a card twice
(v11). What is left is v12, one more proof of the plugin system; it changes
nothing above the capture layer.

Alongside the versions: a terminal UI over the whole API (`docs/tui.md`).

Each version's own note: `docs/v1.md` … `docs/v11.md`.
