#!/usr/bin/env bash
# Launch the AlphaMELTS reduced-real cache grind for a feedstock set, detached + niced.
# Usage: blast_grind.sh "<space-sep feedstocks>" ["<space-sep campaigns>"] [hours]
# Builds one --feedstock / --campaign flag per value (argparse action="append").
set -euo pipefail
cd ~/repos/regolith-pyrolysis-simulator
FEEDS="$1"
CAMPS="${2:-C2A_continuous C2B C4}"
HOURS="${3:-8}"
fargs=""; for f in $FEEDS; do fargs="$fargs --feedstock $f"; done
cargs=""; for c in $CAMPS; do cargs="$cargs --campaign $c"; done
mkdir -p ~/cache-grind
# Populate-mode (no --validate-replay): each shard merges into blitz.db immediately, so the
# cache persists incrementally and a crash loses only in-progress compute. Determinism already
# proven (e2e + committed fix 3037b9c); replay-validate separately on the merged db if needed.
nohup nice -n 15 .venv/bin/python scripts/populate_reduced_real_cache.py \
  $fargs $cargs --backend alphamelts --require-magemin \
  --hours "$HOURS" --wall-cap-s 28800 \
  --db "$HOME/cache-grind/blitz.db" --json-out "$HOME/cache-grind/blitz.json" \
  > "$HOME/cache-grind/blitz.log" 2>&1 &
echo "PID=$!"
