#!/usr/bin/env bash
# Reproducible capture-overhead benchmark. See benchmark/README.md.
#
#   benchmark/run.sh            full run: light, medium, heavy load
#   benchmark/run.sh --quick    smoke run, a few seconds
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

cd "$ROOT"
exec "$PYTHON" benchmark/run.py "$@"
