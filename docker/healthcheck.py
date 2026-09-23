"""Container health, by role.

Each role answers on a different port, and the all-in-one container has to
answer on all of them, so the check reads INFRAREPLAY_ROLE rather than
assuming the API is local.
"""

import os
import sys

import httpx

_TIMEOUT_S = 3

_API = f"http://127.0.0.1:{os.environ.get('INFRAREPLAY_API_PORT', '8000')}/health"
_SHOP = f"http://127.0.0.1:{os.environ.get('DEMO_SHOP_PORT', '3000')}/health"
_DASHBOARD = f"http://127.0.0.1:{os.environ.get('INFRAREPLAY_DASHBOARD_PORT', '8080')}/"

_URLS_BY_ROLE = {
    "api": [_API],
    "shop": [_SHOP],
    "dashboard": [_DASHBOARD],
    "all": [_API, _SHOP, _DASHBOARD],
}


def main() -> int:
    role = os.environ.get("INFRAREPLAY_ROLE", "all")

    for url in _URLS_BY_ROLE.get(role, [_API]):
        try:
            httpx.get(url, timeout=_TIMEOUT_S).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"{role}: {url} unhealthy: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
