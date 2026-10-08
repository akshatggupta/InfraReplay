# 04 · Running it, and the demo script

## The TUI

```bash
infractl tui      # needs the API running; honours INFRAREPLAY_API_URL / _API_KEY
```

`1`–`5` switch tabs (overview, recordings, captures, audit, plugins), `enter`
opens, `r` replays / refreshes, `x` stops a capture, `e` exports, `q` quits.
Full guide: `docs/tui.md`.

## Observability stack

```bash
docker compose --profile observability up --build
# Prometheus http://localhost:9090   Grafana http://localhost:3001
```

## Turning security on

```bash
export INFRAREPLAY_API_KEYS="ci:<secret>"      # server
export INFRAREPLAY_API_KEY="<secret>"          # every client, incl. the demo shop's agent
export INFRAREPLAY_STORAGE_PLUGIN=storage_encrypted
export INFRAREPLAY_ENCRYPTION_KEY="$(infractl security keygen)"
infractl audit list
```

## Docker, one container (simplest)

```bash
docker build -t infrareplay . && docker run --rm --name infrareplay \
  -p 8000:8000 -p 8080:8080 -p 3000:3000 -p 8081:8081 \
  -v infrareplay-data:/data infrareplay
```

Open **http://localhost:8080**. Build takes ~57s, boot to healthy ~22s, and
the fixture recordings are seeded automatically.

| Port | What |
|---|---|
| 8080 | dashboard (the frontend) |
| 8000 | API, `/docs` for OpenAPI |
| 3000 | demo shop — the app being recorded |
| 8081 | where `infractl capture start --listen :8081` listens |

Drive it from another terminal: `docker exec -it infrareplay infractl …`
Inside this container the shop is local, so `--upstream :3000` is correct.

## Docker Compose (separate services + Postgres)

```bash
docker compose up --build
```

Roles come from `INFRAREPLAY_ROLE` (`api` · `shop` · `dashboard` · `all`) on
the one image; a one-shot `seed` service seeds the fixtures. **Containers
reach each other by service name**, so here it is `http://shop:3000`:

```bash
docker compose exec api infractl capture start --listen :8081 --upstream http://shop:3000
docker compose exec api infractl capture stop <recording-id>
docker compose exec api infractl replay <recording-id> --target http://shop:3000
```

Switch the bug off: `DEMO_SHOP_LOCK_INVENTORY=1 docker compose up -d --force-recreate shop`
Tear down: `docker compose down -v`

Postgres is deliberately **not** published to the host.

## Local, no Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

uvicorn infrareplay.api.app:app --port 8000        # terminal 1
uvicorn examples.demo_shop.app:app --port 3000     # terminal 2
python -m infrareplay.dashboard.app                # terminal 3 -> :8080
```

The existing venv is at `.venv/` and already has everything, including
`setuptools` (needed for `--no-build-isolation` reinstalls).

## CLI reference

| Command | Purpose |
|---|---|
| `infractl demo seed` | create the clean + buggy fixture recordings |
| `infractl capture start --listen :8081 --upstream :3000` | start a live capture proxy |
| `infractl capture list` / `capture stop <id>` | show / finalise live captures |
| `infractl recordings list [--kind capture\|replay]` | list recordings |
| `infractl recordings show <id>` | full recording as JSON |
| `infractl recordings export <id> -o f.json` | portable bundle |
| `infractl recordings validate f.json` | check a bundle without importing |
| `infractl recordings import f.json` | load a bundle (re-keys on collision) |
| `infractl replay <id> --target mock\|<url>` | replay + compare |
| `infractl replay <id> --concurrency N --sub old=new --auto-subs` | reproduce races, swap dynamic values |
| `infractl compare <replay-id>` | show a replay's comparison |
| `infractl projects list` | recordings per project |
| `infractl plugins list` | registered plugins |
| `infractl tui` | the terminal UI |
| `infractl audit list [--action …] [--recording …]` | audit log |
| `infractl security keygen` | key for encryption at rest |

## Tests

```bash
pytest                    # 57 passed, 1 skipped with nothing running
```

The skip is `tests/integration/test_dashboard_pages.py`, which renders every
dashboard page and skips unless a stack is live on :8000 with at least one
capture and one completed replay. `pyproject.toml` adds
`-p nicegui.testing.user_plugin` so that fixture exists (the full NiceGUI
plugin needs selenium, which is not installed).

The integration suite starts a real API, a real demo shop and a real capture
proxy on real ports — `tests/support/live.py` (`LiveServer`) and
`tests/integration/conftest.py` (the `stack` fixture, plus `capture`,
`replay`, `reset_shop` helpers).

## The presentation demo, in order

```bash
# 1. everything up (Docker command above), dashboard at :8080

# 2. synthetic first — no infrastructure needed
infractl demo seed
infractl replay <buggy-id> --target mock       # MATCH 4 / DIFFERENT 2

# 3. real traffic
infractl capture start --listen :8081 --upstream :3000
curl -X POST localhost:8081/api/v1/checkout \
  -H 'content-type: application/json' \
  -H 'x-demo-user: dana' \
  -H 'authorization: Bearer secret-token' \
  -d '{"cart_id":"c1","items":[{"sku":"AEROPRESS-GO","qty":1}],
       "payment_method":{"type":"card","token":"tok_live_x2"}}'
infractl capture stop <id>
# 16 events: 1 HTTP request, 7 SQL statements, one correlation id,
# authorization shown as ***REDACTED***

# 4. dashboard: waterfall, expand any query, see the masked header

# 5. replay at the live shop, then at a production-looking host
curl -X POST 'localhost:3000/api/v1/reset?stock=5'
infractl replay <id> --target http://127.0.0.1:3000          # MATCH 8
infractl replay <id> --target https://api.production.example.com   # refused

# 6. the bug (the money shot) — see docs/v6.md
curl -X POST 'localhost:3000/api/v1/reset?stock=1'
infractl capture start --listen :8082 --upstream :3000 --title "oversell race"
#   fire two checkouts at once through :8082, then:
curl localhost:3000/api/v1/orders    # {"confirmed_orders": 2, "stock_left": 1}
infractl capture stop <id>
curl -X POST 'localhost:3000/api/v1/reset?stock=1'
infractl replay <id> --target http://127.0.0.1:3000 --concurrency 2
#   MATCH 16, DIFFERENT 0  -> bug reproduced from the recording
#   now restart the shop with DEMO_SHOP_LOCK_INVENTORY=1, reset, replay again
#   MATCH 12, DIFFERENT 2, MISSING 2
#   DIFFERENT postgres.result  rows_affected 1 -> 0
#   DIFFERENT http.response    status 201 -> 409 {'error': 'out_of_stock'}
```

## Demo shop endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | status + which reservation mode is live |
| `GET` | `/api/v1/inventory` | stock per SKU |
| `GET` | `/api/v1/orders` | confirmed orders vs stock left — how an oversell shows |
| `POST` | `/api/v1/reset?stock=N` | reseed, N units per SKU |
| `POST` | `/api/v1/checkout` | the recorded workflow (needs `x-demo-user: dana`) |

A card token starting `tok_decline`, or a total over £500, is declined by the
fake gateway — deterministic, so a replay reproduces it.
