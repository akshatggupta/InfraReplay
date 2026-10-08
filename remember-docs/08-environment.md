# 08 · Environment, ports, links

## Machine and paths

| Thing | Value |
|---|---|
| Project root | `/mnt/d/InfraReplay` |
| Platform | WSL2 on Windows (`Linux 5.15.167.4-microsoft-standard-WSL2`) |
| Python | 3.12.3, venv at `.venv/` (has `setuptools`, needed for `--no-build-isolation`) |
| Git remote | `git@github.com:akshatggupta/InfraReplay.git` |
| Branch | `main`, clean, in sync at `ee7a10c` |
| Docker | 27.2.0, Compose v2.29.2 — WSL integration had to be enabled in Docker Desktop |

Local state files (all gitignored): `infrareplay.db`, `demo_shop.db`,
`data/blobs/`, `.pytest_cache/`, `.venv/`.

## Ports

| Port | Service | Notes |
|---|---|---|
| 8000 | API | `/docs` for OpenAPI |
| 8080 | NiceGUI dashboard | the frontend |
| 3000 | demo shop | the app being recorded |
| 8081 | capture proxy | only while a capture session is running |
| 5432 | — | **taken by another container (`rss-db`)**; Compose no longer binds it |

Other containers on this machine, unrelated: `rss-db` (5432),
`vara5-pg-local-db-1` (55432).

## Environment variables

**InfraReplay** (`infrareplay/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `INFRAREPLAY_DATABASE_URL` | `sqlite+aiosqlite:///./infrareplay.db` | metadata store |
| `INFRAREPLAY_BLOB_ROOT` | `./data/blobs` | blob storage root |
| `INFRAREPLAY_API_URL` | `http://127.0.0.1:8000` | where CLI, dashboard and agent post |
| `INFRAREPLAY_MAX_BODY_BYTES` | `65536` | above this a captured body becomes a truncation note |
| `INFRAREPLAY_CAPTURE_TIMEOUT_S` | `30` | proxy upstream timeout |
| `INFRAREPLAY_IGNORE_FIELDS` | — | comma-separated payload keys dropped before comparison |
| `INFRAREPLAY_DASHBOARD_PORT` | `8080` | dashboard port |
| `INFRAREPLAY_ROLE` | `all` | Docker only: `api` · `shop` · `dashboard` · `all` |
| `INFRAREPLAY_SEED` | `1` | Docker only: `0` skips boot seeding |
| `INFRAREPLAY_API_PORT` | `8000` | Docker entrypoint only |

Production-target refusal matches `prod`, `production`, `live` in the target
string (`PROD_TARGET_MARKERS`).

**Demo shop** (`examples/demo_shop/app.py`):

| Variable | Default | Meaning |
|---|---|---|
| `DEMO_SHOP_DATABASE_URL` | `sqlite+aiosqlite:///./demo_shop.db` | its own store |
| `DEMO_SHOP_LOCK_INVENTORY` | `0` | `1` fixes the oversell bug |
| `DEMO_SHOP_RACE_WINDOW_S` | `0.05` | width of the read-then-write window |
| `DEMO_SHOP_PORT` | `3000` | port when run as `__main__` or in Docker |

## Correlation headers (the wire contract)

```
x-infrareplay-recording-id     which recording these events belong to
x-infrareplay-correlation-id   which request group
```

Set by the proxy and by the replay engine; read by the agent's middleware.
Both are in `_VOLATILE_HEADERS`, so they never show up as a diff.

## Links

- **Status page (Artifact)** — https://claude.ai/artifact/S3DdTUBbhoUmkQKr96oBDd
  Private: the professor cannot open it until it is shared from the page's
  Share menu. Same content lives in the repo at `docs/status.html`, which
  opens as a standalone file.
- Repo — `git@github.com:akshatggupta/InfraReplay.git`

## Session tooling notes

- Claude Code updated 2.1.280 → 2.1.281 on 2026-09-24.
- MCP: `claude.ai Claude Docs` connected. `browseros-neo` is configured but
  **failed to connect** (`CONNECT_TIMEOUT` after 30s) as of 2026-10-08.
- No browser or computer-use tools in this session; see
  `05-gotchas.md` §14 for how visual checks were actually done.
