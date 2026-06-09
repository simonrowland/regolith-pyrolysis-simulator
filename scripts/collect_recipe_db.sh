#!/usr/bin/env bash
# Collect recipe-DB studies from all studio nodes into ONE laptop runs dir.
# The per-(feedstock,campaign) study dirs are disjoint across nodes, so they
# merge into a single browsable runs root (point OPTIMIZER_RUNS_DIR at it).
# Usage: collect_recipe_db.sh [dest_dir] [node1 node2 ...]
set -uo pipefail
DEST="${1:-$HOME/recipe-db-collected}"
shift || true
NODES=("$@")
[ ${#NODES[@]} -eq 0 ] && NODES=(mac-studio-256-1 mac-studio-256-2 mac-studio-256-3)
mkdir -p "$DEST/runs"
for n in "${NODES[@]}"; do
  echo "collecting $n ..."
  rsync -a "$n:recipe-db/runs/" "$DEST/runs/" 2>/dev/null \
    || scp -rq "$n:recipe-db/runs/." "$DEST/runs/" 2>/dev/null || echo "  (no runs on $n)"
  scp -q "$n:recipe-db/build-summary.json" "$DEST/build-summary-$n.json" 2>/dev/null || true
done
n_studies=$(find "$DEST/runs" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
n_dbs=$(find "$DEST/runs" -name cache.sqlite 2>/dev/null | wc -l | tr -d ' ')
echo "collected to $DEST/runs : ${n_studies} study dirs, ${n_dbs} cache.sqlite"
du -sh "$DEST/runs" 2>/dev/null
