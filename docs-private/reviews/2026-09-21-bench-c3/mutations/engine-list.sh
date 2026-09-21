#!/bin/sh
set -eu
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
PYTHON=${PYTHON:-python3}
exec "$PYTHON" docs-private/reviews/2026-09-21-bench-c3/mutations/run_guard.py engine-list
