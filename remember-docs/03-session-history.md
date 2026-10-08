# 03 · What this session did

Session ran **2026-09-21 → 2026-10-08**. Starting point: v1 complete,
15 tests, last commit `2470a16`. Ending point: v1–v7 complete, 35 tests,
Docker verified, everything pushed at `ee7a10c`.

## The ask, and how it was read

> "check all the log files and docs of this project and i want this project
> 60% to be ready … make sure project is working … there are several versions
> of this project … once done make a html page telling what you done till now
> what previously has been done"

`idea.md` has a twelve-version roadmap with only v1 done, so 60% was read as
**v2 through v7** (7/12 ≈ 58%). That reading was stated up front and is what
shipped.

## Commits, oldest first

| Commit | What and why |
|---|---|
| `990676b` | **FIX: Store nanosecond clocks in 64-bit columns.** `timestamp_ns`/`duration_ns` were `Integer`; ~1.8e18 overflows Postgres int4, so every recording under Compose would have failed on insert. Added migration `0002_bigint_clock`. |
| `d803c9b` | **FEAT: Add portable recording bundles.** `recording/portable.py` — `bundle_version`, `schema_version`, `rekey()` for id collisions. |
| `a1a1333` | **REFACTOR: Split the recording lifecycle into three steps.** open / append / finish, used by both capture and replay; `correlate.py` added. |
| `7d11004` | **FEAT: Add the HTTP capture proxy plugin.** v2. Starlette proxy + `ReverseProxy`, body limits, `config.py` additions, entry points. |
| `bba6f2c` | **FEAT: Capture in-app SQL and tie it to its request.** v3. `capture/context.py`, `plugins/postgres_capture/`, `agent/`. |
| `8a282e1` | **FEAT: Add the demo shop and its inventory bug.** v6's app (built early, because v3 needed something real to record). |
| `cf80447` | **FEAT: Replay recordings against live targets.** v4. `http_target.py`, async engine dispatch, substitution, linking, `--concurrency`, safety check moved ahead of the recording. |
| `b255c8e` | **FEAT: Normalise volatile values before comparing.** v5. `comparison/normalize.py`, both comparators updated. |
| `2041a14` | **FEAT: Expose captures, live replay and bundles.** API + CLI surface for v2/v4/v7. |
| `e192371` | **FEAT: Drive capture and live replay from the dashboard.** `/captures` page, replay dialog (target, concurrency, unsafe), export button. |
| `42038b2` | **TEST: Cover live capture, replay and bundles.** Integration suite on real ports + the skippable dashboard smoke test. |
| `6875a09` | **CHORE: Add the demo shop to the Docker deployment.** |
| `e8c2e3c` | **DOCS: Document the v2 through v7 build.** `docs/v2.md`–`v7.md`, `docs/roadmap.md`, new README, "As Built" section in `architecture.md`, rebuilt `docs/status.html`. |
| `7897790` | **CHORE: Run the whole stack from one container.** Role-based `docker/entrypoint.sh`, `docker/seed.py`, `docker/healthcheck.py`, `.dockerignore`, Compose rewritten around roles. |
| `ee7a10c` | **FIX: Stop publishing Postgres to the host.** 5432 collided with an existing Postgres; also documented `http://shop:3000` for Compose. |

The user pushed through `e8c2e3c` themselves between sittings; `7897790` and
`ee7a10c` were pushed from this session on request.

## Verification actually performed

Not just unit tests — the demo was run for real, twice over.

**Native (uvicorn on the host):** `infractl demo seed`; live capture through
`:8081` producing 16 events (1 HTTP request + 7 SQL statements, one
correlation id, `authorization: ***REDACTED***`); replay against the live
shop → `MATCH: 8, DIFFERENT: 0`; replay twice → stable; production target
refused with exit 1; export → validate → import → replay; `projects list`;
`plugins list` showing all seven.

**The v6 bug story, end to end:** one unit in stock, two concurrent
checkouts captured → `{"confirmed_orders": 2, "stock_left": 1}` (one unit
sold twice); replay with `--concurrency 2` against the buggy shop →
`MATCH: 16, DIFFERENT: 0` (bug reproduced); restart with
`DEMO_SHOP_LOCK_INVENTORY=1`, replay the same recording →
`MATCH: 12, DIFFERENT: 2, MISSING: 2` with
`rows_affected 1 -> 0` and `status 201 -> 409 {'error': 'out_of_stock'}`.

**Dashboard:** all four pages rendered via NiceGUI's headless `user` fixture
(`-p nicegui.testing.user_plugin`) against the live stack. There was never a
browser in this session.

**Docker (after the user enabled WSL integration):** build 57s; all-in-one
container healthy in ~22s; capture + replay inside the container; `docker
stop` returned in 0.65s (SIGTERM trap); volume persistence confirmed with
`4 recordings already stored; not seeding`. Compose: all four services
healthy on Postgres, and **cross-container capture** — proxy in `api`, SQL
from `shop` — produced 16 events under one correlation id with
`timestamp_ns = 1790139326678423871`, which is the BigInteger fix proving
itself.

## Other work in the session

- `docs/status.html` rebuilt as the presentation page (what was built before
  vs what this push added, a real transcript, the cross-process diagram, the
  roadmap, a runbook, and an explicit "what it does not do yet" section).
  Also published as a **private** Artifact — see `08-environment.md`.
- `claude update`: 2.1.280 → 2.1.281.
- Docker cleanup at the user's request: container, volume
  `infrareplay-data`, and the `infrareplay:latest` image all removed. (The
  image was already gone when `docker rmi` ran — reported honestly rather
  than claimed as a success.)
- Memory updated: scope note now says v1–v7 shipped, v8+ not started.
