# InfraReplay

Record a real HTTP + PostgreSQL workflow, store it as a portable
recording, replay it elsewhere, and get an automatic diff.

**Core loop:** capture → correlate → store → replay → compare.

## Status: v7 of 12 — real capture, real replay, real bug caught

The pipeline runs on **real traffic**, end to end:

- a reverse proxy records HTTP;
- SQLAlchemy instrumentation inside the app records the SQL each request
  triggers, tagged with the same correlation id;
- replay sends those requests at a live service (one at a time, or
  concurrently) and records what really comes back — including the SQL the
  target ran;
- the comparison normalises away fresh ids and clocks, so what it reports
  is a real difference.

`MockCapturePlugin` is still there and still used by `infractl demo seed`,
the unit tests and CI — it is how the whole thing was proved before any
real capture code existed. See `docs/roadmap.md` for what is done and what
is not.

## Quick start (local, no Docker)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

# terminal 1 — API
uvicorn infrareplay.api.app:app --port 8000

# terminal 2 — the app to record
uvicorn examples.demo_shop.app:app --port 3000

# terminal 3 — dashboard
python -m infrareplay.dashboard.app        # http://127.0.0.1:8080
```

## Quick start (Docker — one command)

Backend, demo shop and dashboard in a single container, seeded on boot:

```bash
docker build -t infrareplay . && docker run --rm --name infrareplay \
  -p 8000:8000 -p 8080:8080 -p 3000:3000 -p 8081:8081 \
  -v infrareplay-data:/data infrareplay
```

Then open **http://localhost:8080**.

| Port | What |
|---|---|
| 8080 | dashboard — the frontend |
| 8000 | API (`/docs` for OpenAPI) |
| 3000 | demo shop, the app being recorded |
| 8081 | where `infractl capture start --listen :8081` listens |

Recordings live in the `infrareplay-data` volume, so they survive a
restart. Drive the CLI inside the running container:

```bash
docker exec -it infrareplay infractl recordings list
docker exec -it infrareplay infractl capture start --listen :8081 --upstream :3000
docker exec -it infrareplay infractl replay <recording-id> --target http://127.0.0.1:3000
```

Stop it with `Ctrl-C`, or `docker stop infrareplay` from another terminal.

## Quick start (Docker Compose — separate services + Postgres)

```bash
docker compose up --build
```

Same ports, but each service is its own container and the metadata store
is Postgres instead of SQLite. `INFRAREPLAY_ROLE` (`api` · `shop` ·
`dashboard` · `all`) is what selects a role from the one image.

To demo the inventory fix, restart the shop with the flag set:

```bash
DEMO_SHOP_LOCK_INVENTORY=1 docker compose up -d --force-recreate shop
```

## Demo transcript

Synthetic first — no infrastructure needed:

```
$ infractl demo seed
rec_3ad6c830a44a  Checkout — clean run
rec_836f26303bbd  Checkout — payment step fails

$ infractl replay rec_836f26303bbd --target mock
summary: {'MATCH': 4, 'DIFFERENT': 2, 'MISSING': 0, 'NEW': 0, 'ERROR': 0}
```

Then the real thing:

```
$ infractl capture start --listen :8081 --upstream :3000
rec_71bdd9bf3688  listening :8081 -> http://127.0.0.1:3000

$ curl -X POST localhost:8081/api/v1/checkout -H 'x-demo-user: dana' \
       -H 'authorization: Bearer secret-token' -d '{...}'

$ infractl capture stop rec_71bdd9bf3688
rec_71bdd9bf3688  [completed]  16 events

 0 http.request     POST /api/v1/checkout        authorization: ***REDACTED***
 1 postgres.query   SELECT users...
 3 postgres.query   SELECT inventory...
 5 postgres.query   INSERT INTO orders...
 7 postgres.query   INSERT INTO order_items...
 9 postgres.query   UPDATE inventory...
11 postgres.query   INSERT INTO payments...
13 postgres.query   UPDATE orders SET status...
15 http.response    201

$ infractl replay rec_71bdd9bf3688 --target http://127.0.0.1:3000
summary: {'MATCH': 8, 'DIFFERENT': 0, 'MISSING': 0, 'NEW': 0, 'ERROR': 0}

$ infractl replay rec_71bdd9bf3688 --target https://api.production.example.com
error 400: target looks like production; pass unsafe to override
```

And the bug the demo shop is built around — one unit in stock, two
shoppers at once, replayed against the buggy app and then the fixed one:

```
$ infractl replay rec_3856638fb164 --target http://127.0.0.1:3000 --concurrency 2
summary: {'MATCH': 16, 'DIFFERENT': 0, ...}          # bug reproduced

$ DEMO_SHOP_LOCK_INVENTORY=1 ...restart the shop...
$ infractl replay rec_3856638fb164 --target http://127.0.0.1:3000 --concurrency 2
summary: {'MATCH': 12, 'DIFFERENT': 2, 'MISSING': 2, ...}

DIFFERENT  postgres.result  rows_affected 1 -> 0
DIFFERENT  http.response    status 201 -> 409   body: {'error': 'out_of_stock'}
```

Full walkthrough: `docs/v6.md`.

In the dashboard: recordings list → click a recording → request-scoped
waterfall (nested HTTP→DB events with duration bars, expand any event for
its SQL / headers / body) → **Replay** (mock or a live URL) →
captured-vs-replayed comparison with per-field diffs. **Captures** starts
and stops a live proxy from the browser.

## CLI

| Command | Purpose |
|---|---|
| `infractl demo seed` | create the clean + buggy fixture recordings |
| `infractl capture start --listen :8081 --upstream :3000` | start a live capture proxy |
| `infractl capture list` / `stop <id>` | show / finalise live captures |
| `infractl recordings list [--kind capture\|replay]` | list recordings |
| `infractl recordings show <id>` | full recording as JSON |
| `infractl recordings export <id> -o f.json` | portable bundle |
| `infractl recordings validate f.json` | check a bundle without importing |
| `infractl recordings import f.json` | load a bundle into this instance |
| `infractl replay <id> --target mock\|<url>` | replay + compare |
| `infractl replay <id> --concurrency N --sub old=new --auto-subs` | reproduce races, swap dynamic values |
| `infractl compare <replay-id>` | show a replay's comparison |
| `infractl projects list` | recordings per project |
| `infractl plugins list` | registered plugins |

## Tests

```bash
pytest
```

35 tests. The integration suite starts a real API, a real demo shop and a
real capture proxy on real ports — nothing about capture, replay or
comparison is stubbed there.

## Layout

```
infrareplay/
  schema/       Pydantic Event / Recording / ReplayRun / ComparisonResult
  contracts/    ABCs: capture, storage, redaction, comparator
  plugins/      registry + built-ins (mock_capture, http_capture,
                postgres_capture, storage_fs, redaction_default,
                http/postgres comparators)
  capture/      live sessions, correlation context, ingest
  agent/        app-side instrumentation (middleware + ingest client)
  db/           async SQLAlchemy metadata store
  recording/    lifecycle, correlation, export/import
  replay/       engine, live HTTP target, substitution, linking
  comparison/   pair-walk engine + normalisation
  api/          FastAPI
  cli/          Typer (infractl)
  dashboard/    NiceGUI
examples/demo_shop/   the recorded application, with its bug
migrations/           Alembic
```

See `architecture.md` for the system design, `docs/roadmap.md` for build
state, and `docs/v1.md` … `docs/v7.md` for what each version added.
