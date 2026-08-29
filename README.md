# InfraReplay

Record a real HTTP + PostgreSQL workflow, store it as a portable
recording, replay it elsewhere, and get an automatic diff.

**Core loop:** capture → correlate → store → replay → compare.

## Status: v1 — full pipeline, synthetic data

The entire pipeline runs end to end: schema, plugin registry, storage,
replay engine, comparator, API, CLI, dashboard. Capture is **mocked** at
this stage — `MockCapturePlugin` generates a realistic `demo_shop`
checkout: `POST /api/v1/checkout` → auth `SELECT` → `BEGIN` → `INSERT
orders` → `INSERT order_items` → `UPDATE inventory` → `INSERT payments`
→ `COMMIT`, all under one `correlation_id`. Real capture plugins (HTTP
proxy, Postgres instrumentation) plug into this same pipeline in v2/v3
without rewriting anything above.

## Quick start (local, no Docker)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

# terminal 1 — API
uvicorn infrareplay.api.app:app --port 8000

# terminal 2 — dashboard
python -m infrareplay.dashboard.app        # http://127.0.0.1:8080
```

## Quick start (Docker)

```bash
docker compose up --build
infractl demo seed
```

- API: http://localhost:8000  (`/docs` for OpenAPI)
- Dashboard: http://localhost:8080

## Demo transcript

```
$ infractl demo seed
rec_83c0b4f0  Checkout — clean run
rec_071f2ad3  Checkout — payment step fails

$ infractl recordings list
rec_071f2ad3  [capture/completed]  Checkout — payment step fails
rec_83c0b4f0  [capture/completed]  Checkout — clean run

$ infractl replay rec_071f2ad3 --target mock
replay rpl_7b5b0579 against mock
summary: {'MATCH': 4, 'DIFFERENT': 2, 'MISSING': 0, 'NEW': 0, 'ERROR': 0}

$ infractl compare rpl_7b5b0579
MATCH      postgres.result   (auth, order, line items, inventory)
MATCH      postgres.result
MATCH      postgres.result
MATCH      postgres.result
DIFFERENT  postgres.result   {'rows': {'original': [{'state': 'declined', ...}], ...}}
DIFFERENT  http.response     {'status': {'original': 402, 'replayed': 201}, ...}
```

In the dashboard: recordings list → click a recording → request-scoped
waterfall (nested HTTP→DB events with duration bars, expand any event
for its SQL / headers / body) → **Replay against mock** → captured-vs-
replayed comparison with per-field diffs.

## CLI

| Command | Purpose |
|---|---|
| `infractl demo seed` | create the clean + buggy fixture recordings |
| `infractl recordings list [--kind capture\|replay]` | list recordings |
| `infractl recordings show <id>` | full recording as JSON |
| `infractl replay <id> --target mock [--unsafe]` | replay + compare |
| `infractl compare <replay-id>` | show a replay's comparison |
| `infractl plugins list` | registered plugins |

## Tests

```bash
pytest
```

## Layout

```
infrareplay/
  schema/       Pydantic Event / Recording / ReplayRun / ComparisonResult
  contracts/    ABCs: capture, storage, redaction, comparator
  plugins/      registry + built-ins (mock_capture, storage_fs,
                redaction_default, http/postgres comparators)
  db/           async SQLAlchemy metadata store
  recording/    lifecycle — the one source of truth
  replay/       replay engine + value substitution
  comparison/   pair-walk engine
  api/          FastAPI
  cli/          Typer (infractl)
  dashboard/    NiceGUI
migrations/      Alembic
```

See `architecture.md` for the system design and `docs/v1.md` for what v1
does and does not do.
