"""Record real demo traffic once the stack is up — no fixtures.

Runs once at container start so the dashboard and TUI open on real
recordings: real checkouts through a real capture of the shop, with the SQL
it ran and its calls to the payment gateway.

    1. "Checkout — live traffic"     three checkouts, one card declined
    2. "Checkout — oversell race"    two shoppers, one unit, at the same moment

Then the shop is restocked, so the first replay on stage has stock to sell.
Never fatal: a container that cannot record still serves.
"""

import os
import sys
import time

import httpx

API_URL = os.environ.get("INFRAREPLAY_API_URL", "http://127.0.0.1:8000")
SHOP_URL = os.environ.get("INFRAREPLAY_DEMO_SHOP_URL", "http://127.0.0.1:3000")
SEED = os.environ.get("INFRAREPLAY_SEED", "1") != "0"
HEADERS = {"x-api-key": os.environ["INFRAREPLAY_API_KEY"]} if os.environ.get("INFRAREPLAY_API_KEY") else {}

_TIMEOUT_S = 120
_POLL_S = 0.5
_RESTOCK = 100

_TRAFFIC = (
    {"title": "Checkout — live traffic", "checkouts": 3, "declined": 1},
    {"title": "Checkout — oversell race", "checkouts": 2, "declined": 0, "concurrent": True, "stock": 1},
)


def wait_for(url: str) -> bool:
    deadline = time.monotonic() + _TIMEOUT_S

    while time.monotonic() < deadline:
        try:
            httpx.get(f"{url}/health", timeout=2).raise_for_status()
            return True
        except httpx.HTTPError:
            time.sleep(_POLL_S)

    return False


def main() -> int:
    if not SEED:
        return 0

    for name, url in (("api", API_URL), ("shop", SHOP_URL)):
        if not wait_for(url):
            print(f"{name} did not answer at {url}; not recording demo traffic", file=sys.stderr)
            return 0

    try:
        existing = httpx.get(f"{API_URL}/api/recordings", timeout=10, headers=HEADERS).json()

        if existing:
            print(f"{len(existing)} recordings already stored; not recording more")
            return 0

        for body in _TRAFFIC:
            recording = httpx.post(
                f"{API_URL}/api/demo/traffic", json={"upstream": SHOP_URL, **body},
                timeout=60, headers=HEADERS,
            ).json()
            print(f"recorded {recording['recording_id']}  {recording['title']}  "
                  f"({len(recording['events'])} real events)")

        httpx.post(f"{SHOP_URL}/api/v1/reset", params={"stock": _RESTOCK}, timeout=10)

    except (httpx.HTTPError, KeyError) as exc:
        print(f"demo traffic skipped: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
