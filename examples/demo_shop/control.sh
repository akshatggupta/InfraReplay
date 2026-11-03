#!/usr/bin/env bash
# Presenter controls for the demo shop and its payment gateway.
#
#   examples/demo_shop/control.sh restock 1      set every product's stock (1 = race setup)
#   examples/demo_shop/control.sh fix on|off     the inventory fix, as a runtime flag
#   examples/demo_shop/control.sh orders         confirmed orders vs stock left
#   examples/demo_shop/control.sh ledger         the payment gateway's charges
set -euo pipefail

SHOP="${SHOP_URL:-http://localhost:3000}"
GATEWAY="${GATEWAY_URL:-http://localhost:4000}"

case "${1:-}" in
  restock) curl -s -X POST "$SHOP/api/v1/reset?stock=${2:?how many units}" ;;
  fix)
    case "${2:-}" in
      on)  curl -s -X POST "$SHOP/api/v1/admin/inventory-lock?enabled=true" ;;
      off) curl -s -X POST "$SHOP/api/v1/admin/inventory-lock?enabled=false" ;;
      *)   echo "usage: $0 fix on|off" >&2; exit 64 ;;
    esac ;;
  orders)  curl -s "$SHOP/api/v1/orders" ;;
  ledger)  curl -s "$GATEWAY/v1/charges" ;;
  *)       sed -n '2,7p' "$0"; exit 64 ;;
esac
echo
