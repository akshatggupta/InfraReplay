#!/usr/bin/env bash
#
# One image, four roles.
#
#   INFRAREPLAY_ROLE=all         API + demo shop + dashboard in one container
#   INFRAREPLAY_ROLE=api         the API alone            (compose)
#   INFRAREPLAY_ROLE=shop        the demo shop alone      (compose)
#   INFRAREPLAY_ROLE=dashboard   the dashboard alone      (compose)
#
# Any arguments passed to the container are run instead, so
# `docker run ... infrareplay infractl recordings list` still works.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

ROLE="${INFRAREPLAY_ROLE:-all}"

API_PORT="${INFRAREPLAY_API_PORT:-8000}"
SHOP_PORT="${DEMO_SHOP_PORT:-3000}"
DASHBOARD_PORT="${INFRAREPLAY_DASHBOARD_PORT:-8080}"

start_api() {
  uvicorn infrareplay.api.app:app --host 0.0.0.0 --port "$API_PORT" &
}

start_shop() {
  uvicorn examples.demo_shop.app:app --host 0.0.0.0 --port "$SHOP_PORT" &
}

start_dashboard() {
  python -m infrareplay.dashboard.app &
}

case "$ROLE" in
  api)       exec uvicorn infrareplay.api.app:app --host 0.0.0.0 --port "$API_PORT" ;;
  shop)      exec uvicorn examples.demo_shop.app:app --host 0.0.0.0 --port "$SHOP_PORT" ;;
  dashboard) exec python -m infrareplay.dashboard.app ;;
  all)       ;;
  *)
    echo "unknown INFRAREPLAY_ROLE: $ROLE (api|shop|dashboard|all)" >&2
    exit 64
    ;;
esac

# ---- all-in-one -------------------------------------------------------------

pids=()

shutdown() {
  kill "${pids[@]}" 2>/dev/null || true
  wait || true
}

trap shutdown TERM INT

start_api
pids+=("$!")

start_shop
pids+=("$!")

# Seeds the two fixture recordings once the API answers, so the dashboard
# has something in it the moment it opens. INFRAREPLAY_SEED=0 turns it off.
python "$HERE/seed.py"

start_dashboard
pids+=("$!")

echo
echo "InfraReplay is up:"
echo "  dashboard  http://localhost:${DASHBOARD_PORT}"
echo "  API        http://localhost:${API_PORT}        (/docs for OpenAPI)"
echo "  demo shop  http://localhost:${SHOP_PORT}"
echo

# Exit as soon as any one of them dies, so the container's status is honest.
wait -n
shutdown
