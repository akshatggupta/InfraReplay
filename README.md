# InfraReplay

Record a real HTTP + PostgreSQL workflow — including the calls the app makes
to other services — store it as a portable recording, replay it elsewhere,
and get an automatic diff.

**Core loop:** capture → correlate → store → replay → compare.

## Status: v11 of 12 — real traffic, real services, measured

Everything in the demo path is real:

- a reverse proxy records the HTTP the client sends;
- the app's agent records every SQL statement each request runs, and every
  call it makes to the **payment-gateway service** — all under one
  correlation id;
- replay re-sends the requests to a live shop (one at a time, or concurrently
  to reproduce a race), records what really comes back, and **answers the
  shop's payment calls from the recording**, so a replay never charges a card
  twice — the gateway's own ledger proves it (v11);
- the comparison normalises away fresh ids and clocks and names the exact
  field that changed;
- secrets are masked before anything is stored, snapshots can be encrypted at
  rest, the API can require keys, every action is audited (v8);
- Prometheus metrics, JSON logs and a Grafana dashboard (v9);
- what capture costs under load is **measured and published** (v10).

The demo records **real traffic**, not fixtures: `infractl demo traffic`,
`t` in the TUI, the dashboard's button, and the Docker boot all drive real
checkouts through a real capture. (`MockCapturePlugin` and the `mock` replay
target remain for unit tests and offline use only.) What is done and what is
not: `docs/roadmap.md`.

## Run it — Docker, one command

```bash
docker build -t infrareplay . && docker run --rm --name infrareplay \
  -p 8000:8000 -p 8080:8080 -p 3000:3000 -p 4000:4000 -p 8081:8081 \
  -v infrareplay-data:/data infrareplay
```

On boot it records two real sessions — ordinary checkout traffic and the
oversell race — so the dashboard and TUI open on real recordings.

Port already taken? Map it elsewhere, e.g. `-p 8088:8080` for the dashboard
(Compose: `INFRAREPLAY_DASHBOARD_HOST_PORT=8088 docker compose up`).

| Port | What |
|---|---|
| 8080 | web dashboard |
| 8000 | API (`/docs` for OpenAPI, `/metrics` for Prometheus) |
| 3000 | demo shop — the app being recorded |
| 4000 | payment gateway — the shop's external service; ledger at `/v1/charges` |
| 8081 | where `infractl capture start --listen :8081` listens |

The terminal UI and CLI run inside the container:

```bash
docker exec -it infrareplay infractl tui
docker exec -it infrareplay infractl demo traffic
```

## Run it — locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,benchmark]"

uvicorn infrareplay.api.app:app --port 8000               # terminal 1 — API
uvicorn examples.payment_gateway.app:app --port 4000      # terminal 2 — payment gateway
uvicorn examples.demo_shop.app:app --port 3000            # terminal 3 — the shop
python -m infrareplay.dashboard.app                       # terminal 4 — dashboard :8080
infractl tui                                              # or the terminal UI
```

## The demo — a real run, unedited

```
$ infractl demo traffic
rec_37640a5acb8b  [completed]  Checkout — live traffic
  62 events · responses 201, 201, 402 · 3 payment-gateway calls

$ examples/demo_shop/control.sh ledger   # the payment gateway's own count
charges: 3

$ infractl replay rec_37640a5acb8b
replay rpl_3f93a87c3698 against http://127.0.0.1:3000
summary: {'MATCH': 31, 'DIFFERENT': 0, 'MISSING': 0, 'NEW': 0, 'ERROR': 0}

$ examples/demo_shop/control.sh ledger
charges: 3   <- unchanged: the replay charged nobody
```

The bug the demo shop is built around — one unit in stock, two shoppers at
once — recorded, reproduced, fixed with a feature flag, and shown fixed:

```
$ infractl demo race
rec_18b9fde3be97  [completed]  Checkout — oversell race
  36 events · responses 201, 201 · 2 payment-gateway calls

$ examples/demo_shop/control.sh restock 1

$ infractl replay rec_18b9fde3be97 --concurrency 2
replay rpl_2e505b065b8a against http://127.0.0.1:3000
summary: {'MATCH': 18, 'DIFFERENT': 0, 'MISSING': 0, 'NEW': 0, 'ERROR': 0}

$ examples/demo_shop/control.sh fix on

$ examples/demo_shop/control.sh restock 1

$ infractl replay rec_18b9fde3be97 --concurrency 2
replay rpl_6cb860531ba9 against http://127.0.0.1:3000
summary: {'MATCH': 13, 'DIFFERENT': 2, 'MISSING': 3, 'NEW': 0, 'ERROR': 0}

$ infractl compare rpl_6cb860531ba9 | grep -v ^MATCH
DIFFERENT  postgres.result  {'rows_affected': {'original': 1, 'replayed': 0}}
MISSING    http.response    
MISSING    postgres.result  
MISSING    postgres.result  
DIFFERENT  http.response    {'status': {'original': 201, 'replayed': 409}, ...}
```

With the fix, the reserving `UPDATE` changes no row, the second shopper gets a
409 instead of a 201, and that shopper's payment call and its two SQL
statements never happen — which is exactly what the MISSING rows say. The full
run, including the production-target refusal, the benchmark and the audit log:
`docs/demo-transcript.txt`.

## What capture costs — measured (v10)

`benchmark/run.sh` — same workload, direct and through capture, 30 s per run:

| Load | Throughput | Median checkout added | Complete requests | Events dropped |
|---|---:|---:|---:|---:|
| light, 5 users | −12.3% | +33 ms | 100% | 0 |
| medium, 20 users | −24.0% | +150 ms | 100% | 0 |
| heavy, 50 users | −45.7% | +950 ms | 100% | 0 |

The first run found capture unusable under load (−90% to −95% throughput,
and lost events): an HTTP client was built for every request (183 ms each),
and SQLite locks dropped writes. Both are fixed; the story, with every run
kept: `docs/v10.md`. Report and method: `benchmark/results/latest/report.md`.
No performance figure appears in this project without that report behind it.

## Terminal UI

```bash
infractl tui
```

`1`–`6`: overview (live metric tiles and sparklines), recordings, captures,
audit log, plugins, benchmarks. `t` records live traffic, `o` the oversell
race, `enter` opens a recording's request waterfall, `r` replays it, and the
comparison names the exact field that changed. `docs/tui.md`.

## Docker Compose — separate services, Postgres, Grafana

```bash
docker compose up --build                                 # API, shop, gateway, dashboard, Postgres
docker compose --profile observability up --build         # + Prometheus :9090, Grafana :3001
```

Containers reach each other by service name, so the shop is
`http://shop:3000`:

```bash
docker compose exec api infractl replay <recording-id> --target http://shop:3000
```

## Security (v8)

```bash
export INFRAREPLAY_API_KEYS="ci:$(openssl rand -hex 16)"   # server: require keys
export INFRAREPLAY_API_KEY="<that secret>"                  # clients: send one
export INFRAREPLAY_STORAGE_PLUGIN=storage_encrypted          # encrypt snapshots at rest
export INFRAREPLAY_ENCRYPTION_KEY="$(infractl security keygen)"
```

With no keys set the API is open — the dev default — and `/health` says so.

## CLI

| Command | Purpose |
|---|---|
| `infractl demo traffic` | record real checkouts through a live capture of the shop |
| `infractl demo race` | record the oversell race (stock 1, two shoppers at once) |
| `infractl capture start --listen :8081 --upstream :3000` / `stop <id>` | a live capture proxy |
| `infractl recordings list` / `show <id>` | recordings |
| `infractl replay <id>` | replay against the live shop and compare |
| `infractl replay <id> --concurrency N` | reproduce a race |
| `infractl replay <id> --externals live` | really call the shop's external services (off by default) |
| `infractl compare <replay-id>` | a replay's comparison |
| `infractl benchmark show` | the latest benchmark report |
| `infractl audit list` | who captured, replayed, exported or imported what |
| `infractl recordings export` / `import` / `validate` | portable bundles |
| `infractl tui` | the terminal UI |
| `infractl security keygen` | a key for encryption at rest |
| `infractl demo seed` | two **synthetic** fixtures, for tests and offline use |

`examples/demo_shop/control.sh restock N | fix on|off | orders | ledger` drives
the shop and its gateway during a demo.

## Tests

```bash
pytest                 # 72 tests; one skips unless a stack is running
benchmark/run.sh       # the load benchmark, ~7 minutes — run nothing else meanwhile
```

The integration tests start a real API, demo shop, payment gateway and
capture proxy on real ports, and drive the TUI headlessly against them.

## Layout

```
infrareplay/
  schema/  contracts/  plugins/     the event schema, plugin ABCs, built-in plugins
  capture/  agent/                  live capture, the in-app agent, outbound capture (egress.py)
  recording/  replay/  comparison/  lifecycle, replay engine, diffing
  security/  observability/         v8 and v9
  api/  cli/  dashboard/  tui/      one API, three clients
examples/demo_shop/                 the recorded application, with its bug
examples/payment_gateway/           the external service the shop pays through
benchmark/                          v10: run.sh, the Locust workload, reports
docs/                               v1.md … v11.md, roadmap, status page, Grafana
```

`architecture.md` has the system design; `remember-docs/` the handoff notes.
