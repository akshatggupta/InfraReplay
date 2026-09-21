# demo_shop

The application InfraReplay records, replays and diffs. FastAPI +
SQLAlchemy, with one deliberate concurrency bug.

```bash
uvicorn examples.demo_shop.app:app --port 3000
```

| Environment | Default | Meaning |
|---|---|---|
| `DEMO_SHOP_DATABASE_URL` | `sqlite+aiosqlite:///./demo_shop.db` | metadata store |
| `DEMO_SHOP_LOCK_INVENTORY` | `0` | `1` fixes the reservation bug |
| `DEMO_SHOP_RACE_WINDOW_S` | `0.05` | width of the read-then-write window |
| `INFRAREPLAY_API_URL` | `http://127.0.0.1:8000` | where captured SQL is streamed |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | status + which reservation mode is live |
| `GET` | `/api/v1/inventory` | stock per SKU |
| `GET` | `/api/v1/orders` | confirmed orders vs stock left — how an oversell shows |
| `POST` | `/api/v1/reset?stock=N` | reseed, N units per SKU |
| `POST` | `/api/v1/checkout` | the recorded workflow |

`checkout` needs an `x-demo-user: dana` header — a stand-in for a session
cookie, deliberately not a secret one, so the demo still replays after
redaction has masked `Authorization`.

```bash
curl -X POST localhost:3000/api/v1/checkout \
  -H 'content-type: application/json' \
  -H 'x-demo-user: dana' \
  -H 'authorization: Bearer secret-token' \
  -d '{"cart_id":"cart_7b21f0",
       "items":[{"sku":"AEROPRESS-GO","qty":1}],
       "payment_method":{"type":"card","token":"tok_live_x2Kd9pQ"}}'
```

A card token starting `tok_decline`, or a total over £500, is declined by
the fake gateway — deterministic, so a replay reproduces it.

## The bug

`docs/v6.md` has the full walkthrough: one unit in stock, two shoppers at
once, two confirmed orders. `DEMO_SHOP_LOCK_INVENTORY=1` switches the
reservation from read-then-write to a single conditional `UPDATE`, and the
second shopper gets a 409 instead of someone else's coffee maker.
