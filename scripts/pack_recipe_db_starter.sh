#!/usr/bin/env bash
# Pack a collected recipe-DB runs dir into the shippable starter archive
# data/recipe-db-starter.tgz (per-study cache.sqlite + leaderboard/pareto/winner/
# provenance, NO *.log). Study dirs sit at the archive top level (no 'runs/'
# prefix) so install unpacks them straight into the optimizer runs root.
# Usage: pack_recipe_db_starter.sh [collected_runs_dir]
set -euo pipefail
SRC="${1:-$HOME/recipe-db-collected/runs}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/data/recipe-db-starter.tgz"
[ -d "$SRC" ] || { echo "no collected runs dir: $SRC (run collect_recipe_db.sh first)"; exit 1; }
mkdir -p "$(dirname "$OUT")"
tar --exclude='*.log' -czf "$OUT" -C "$SRC" .
echo "packed $(du -h "$OUT" | cut -f1) -> $OUT ($(find "$SRC" -name cache.sqlite | wc -l | tr -d ' ') studies)"
