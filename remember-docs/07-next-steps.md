# 07 · What comes next, and what not to start

## Do not start v8+ without being asked

`idea.md` §8 says work strictly in version order and do not begin v(N+1)
until v(N)'s DoD passes. The user's standing instruction (also in memory) is
that the remaining versions are **not** to be started unprompted. v1–v9 is
the agreed scope (v8 and v9 were explicitly requested on 2026-10-08).

## The remaining version

**v8–v11 are done** — see `docs/v8.md` … `docs/v11.md`.

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
- **Verify the observability profile live** once Docker is on (see `03`) —
  still not seen running as of 2026-11-02.
- **Lift the captured-throughput ceiling**: several API workers, or the capture
  proxy in its own process (the v10 report shows the single API process
  saturating under medium/heavy load). Measure before and after.
- **Capture other egress**: only HTTP through the instrumented client is
  recorded/stubbed (v11). SMTP, raw sockets and libraries with their own
  HTTP stack are not.
- **The TUI has no live stream of a capture in progress** — the timeline
  is loaded once; stop the capture to see it.
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
