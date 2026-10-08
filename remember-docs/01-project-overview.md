# 01 · Project overview

## What it is

**InfraReplay** records a real HTTP + PostgreSQL workflow, stores it as a
portable recording, replays it against a different environment, and produces
an automatic diff. The point is incident reproduction: instead of guessing
what a bug did, you replay the traffic that caused it.

**Core loop:** capture → correlate → store → replay → compare.

It is a Python rewrite of an earlier Go/React spec. Three deliberate
differences, all stated in `idea.md`:

1. Python end to end (backend, CLI, dashboard).
2. Capture sources, storage, redaction and comparators are **plugins**
   discovered through `importlib.metadata` entry points (group
   `infrareplay.plugins`) — no core code imports a concrete plugin.
3. Delivery is versioned v1→v12; every version ends in something you can
   run and demo.

## Stack

| Concern | Choice |
|---|---|
| API | FastAPI + Uvicorn |
| CLI | Typer — the `infractl` command |
| Schema / validation | Pydantic v2, with `schema_version` and a migration registry |
| ORM / migrations | SQLAlchemy 2.0 async + Alembic (metadata only) |
| HTTP capture | Starlette reverse proxy + httpx |
| SQL capture | SQLAlchemy `before/after_cursor_execute` listeners, in the app's process |
| Dashboard | NiceGUI (pure Python, no separate frontend build) |
| Metadata store | SQLite locally, Postgres under Compose |
| Blobs | filesystem `StoragePlugin` (`storage_fs`) |
| Tests | pytest + pytest-asyncio; Textual Pilot for the TUI |
| Security | cryptography (Fernet), constant-time API key check |
| Metrics / logs | prometheus-client, structlog |
| TUI | Textual |

## Package layout

```
infrareplay/
  schema/       Pydantic Event / Recording / ReplayRun / ComparisonResult
  contracts/    ABCs: capture, storage, redaction, comparator
  plugins/      registry + built-ins (mock_capture, http_capture,
                postgres_capture, storage_fs, redaction_default,
                http/postgres comparators)
  capture/      live sessions, correlation context, ingest door
  agent/        app-side instrumentation (ASGI middleware + ingest client)
  db/           async SQLAlchemy metadata store
  recording/    lifecycle, correlation, export/import
  replay/       engine, live HTTP target, substitution, event linking
  comparison/   pair-walk engine + normalisation
  api/          FastAPI
  cli/          Typer (infractl)
  dashboard/    NiceGUI
  tui/          Textual — `infractl tui`
  security/     API keys, audit log, encryption keys (v8)
  observability/ Prometheus metrics, structlog JSON (v9)
examples/demo_shop/   the recorded application, with its bug
migrations/           Alembic
docker/               entrypoint, boot seeding, healthcheck
docs/                 v1..v7 notes, roadmap, status.html
```

~5,850 lines of Python including tests and the demo shop.

## The seven registered plugins

`infractl plugins list` shows eight, by slot:

- **capture** — `mock_capture`, `http_capture`, `postgres_capture`
- **storage** — `storage_fs`, `storage_encrypted` (wraps another by name, Fernet)
- **redaction** — `redaction_default`
- **comparator** — `http_comparator`, `postgres_comparator`

All eight register through `pyproject.toml`'s
`[project.entry-points."infrareplay.plugins"]`, exactly as a third-party
package would. That is the architectural claim the project keeps making, and
it held: adding real capture in v2/v3 changed nothing above the capture layer.

## Roadmap state

| Ver | Scope | State |
|---|---|---|
| v1 | Full pipeline on synthetic data (`MockCapturePlugin`) | **done** (2026-08-29) |
| v2 | Real HTTP capture plugin | **done** |
| v3 | Real SQL capture + correlation | **done** |
| v4 | Replay against live targets | **done** |
| v5 | Comparison hardening | **done** |
| v6 | Demo shop + intentional bug | **done** |
| v7 | CLI completeness, export/import | **done** |
| v8 | Security hardening (encryption at rest, API keys, audit log) | **done** (2026-10-08) |
| v9 | Observability (Prometheus, structlog JSON) | **done** (2026-10-08) |
| v10 | Benchmark suite (Locust, published methodology) | not started |
| v11 | External service mocking on replay | not started |
| v12 | Third-party protocol plugin as a separate package | not started |

**9 of 12 = 75%.** The user first asked for "60% ready" for a college
presentation (v2–v7), then for "the next 2 versions" (v8, v9), then for
"a cool TUI over all these" — `infractl tui`, see `docs/tui.md`.

## Docs that are the source of truth

- `idea.md` — the build spec: the roadmap, each version's Definition of Done,
  the event schema, non-goals. **Written by the user; follow it.**
- `architecture.md` — system design, plus an "As Built (v1 → v7)" section
  added this session for the three things that needed more than the sketch.
- `agent.md` — the user's code-style and commit rules. See `06-conventions.md`.
- `docs/v1.md` … `docs/v7.md` — one note per version: what is real, the DoD,
  and which test proves it.
- `docs/roadmap.md` — the table above, in the repo.
- `docs/status.html` — the presentation page (also published as an Artifact).
- `claude.md` — empty. Nothing lives there.
