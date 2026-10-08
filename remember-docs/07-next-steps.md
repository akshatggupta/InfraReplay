# 07 · What comes next, and what not to start

## Do not start v8+ without being asked

`idea.md` §8 says work strictly in version order and do not begin v(N+1)
until v(N)'s DoD passes. The user's standing instruction (also in memory) is
that v8–v12 are **not** to be started unprompted. v1–v7 is the agreed scope.

## The remaining five versions

**v8 — Security hardening.** Encryption at rest for stored blobs (age or
Fernet), API access control (API keys minimum), an audit log of every
capture/replay action, and tests asserting secrets never appear in stored
recordings or API responses. Today recordings are redacted but not
encrypted, and the API has no auth at all.

**v9 — Observability.** `prometheus-client` metrics (`recordings_total`,
`events_captured_total`, `capture_errors_total`, `replay_total`,
`replay_failures_total`, `storage_bytes`), structlog JSON logs, a scrapeable
`/metrics`, and a Grafana-importable dashboard JSON in `docs/`.

**v10 — Benchmark suite.** Locust against the demo shop with and without
capture: req/s, P50/P95/P99, CPU, memory, captured bytes/sec at light /
medium / heavy load. `benchmark/run.sh` must produce a reproducible report.
Until this exists, publish no performance numbers.

**v11 — External service mocking.** Stub payment/email/SMS/shipping APIs so
replay never hits real external services — and make that the default, with
no flag.

**v12 — Third-party protocol plugin.** One external capture plugin (Kafka is
the best proof) as a *separately installable* package that registers itself
with zero core changes: `pip install infrareplay-kafka-capture` then
`infractl plugins list` shows it. Only after v1–v11 are stable.

## Smaller things worth doing, if asked

- **Replay-time DB capture is unlinked for new events.** `link_streams`
  pairs positionally, which works when both runs produce the same shape; a
  target that runs a different number of queries yields MISSING/NEW rows.
  That is correct behaviour, but a smarter linker (match on normalised SQL)
  would read better in the UI.
- **`rows_affected` is `None` for SELECTs**, so SELECT results always match
  each other trivially. Capturing a row *count* for SELECTs would need a
  different hook than `after_cursor_execute`.
- **The dashboard has no live refresh.** A capture in progress does not
  stream into the page; you stop it and reload.
- **`docker/entrypoint.sh` runs as root** and the all-in-one container holds
  three services. Fine for a demo, wrong for anything else.
- **No CI.** Everything was verified by hand plus `pytest`. A GitHub Actions
  workflow running `pytest` would be cheap and would have caught the stale
  egg-info problem.

## State to be aware of

- Branch `main`, clean, in sync with `origin/main` at `ee7a10c`.
- No Docker image, container or volume for InfraReplay exists any more — the
  user had them deleted on 2026-10-08. `docker build` recreates the image in
  ~57s.
- `infrareplay.db`, `demo_shop.db` and `data/` in the repo root are local
  demo state, gitignored, and may contain recordings from earlier runs.
