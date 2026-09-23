#!/usr/bin/env bash
# Collect recipe-DB studies from all studio nodes into ONE laptop runs dir.
# The per-(feedstock,campaign) study dirs are disjoint across nodes, so they
# merge into a single browsable runs root (point OPTIMIZER_RUNS_DIR at it).
# Usage: collect_recipe_db.sh [dest_dir] [node1 node2 ...]
set -euo pipefail
DEST="${1:-$HOME/recipe-db-collected}"
shift || true
NODES=("$@")
[ ${#NODES[@]} -eq 0 ] && NODES=(mac-studio-256-1 mac-studio-256-2 mac-studio-256-3)
mkdir -p "$DEST/runs"
status=0
for n in "${NODES[@]}"; do
  echo "collecting $n ..."
  if rsync -a "$n:recipe-db/runs/" "$DEST/runs/" 2>/dev/null; then
    :
  elif scp -rq "$n:recipe-db/runs/." "$DEST/runs/" 2>/dev/null; then
    :
  else
    echo "  (no runs on $n)" >&2
    status=1
  fi
  scp -q "$n:recipe-db/build-summary.json" "$DEST/build-summary-$n.json" 2>/dev/null || true
done
n_studies=$(find "$DEST/runs" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
n_dbs=$(find "$DEST/runs" -name cache.sqlite 2>/dev/null | wc -l | tr -d ' ')
echo "collected to $DEST/runs : ${n_studies} study dirs, ${n_dbs} cache.sqlite"
du -sh "$DEST/runs" 2>/dev/null || true
if [ "${n_studies}" -eq 0 ]; then
  echo "collect_recipe_db: zero study dirs collected from requested nodes" >&2
  status=1
fi
exit "$status"
