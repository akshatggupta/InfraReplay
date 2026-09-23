"""Wait for the API, then seed the fixture recordings if it is empty.

Runs once at container start so the dashboard opens with something in it.
Never fatal: a container that cannot seed still serves.
"""

import os
import sys
import time

import httpx

API_URL = os.environ.get("INFRAREPLAY_API_URL", "http://127.0.0.1:8000")
SEED = os.environ.get("INFRAREPLAY_SEED", "1") != "0"

_TIMEOUT_S = 60
_POLL_S = 0.5


def wait_for_api() -> bool:
    deadline = time.monotonic() + _TIMEOUT_S

    while time.monotonic() < deadline:
        try:
            httpx.get(f"{API_URL}/health", timeout=2).raise_for_status()
            return True
        except httpx.HTTPError:
            time.sleep(_POLL_S)

    return False


def main() -> int:
    if not wait_for_api():
        print(f"api did not answer at {API_URL}; skipping seed", file=sys.stderr)
        return 0

    if not SEED:
        return 0

    try:
        existing = httpx.get(f"{API_URL}/api/recordings", timeout=10).json()

        if existing:
            print(f"{len(existing)} recordings already stored; not seeding")
            return 0

        seeded = httpx.post(f"{API_URL}/api/demo/seed", timeout=30).json()

        for recording in seeded:
            print(f"seeded {recording['recording_id']}  {recording['title']}")

    except httpx.HTTPError as exc:
        print(f"seed skipped: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
