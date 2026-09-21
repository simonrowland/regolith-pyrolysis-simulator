#!/bin/sh
set -eu
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
PYTHON=${PYTHON:-python3}
TARGET=simulator/battery/waypoints.py
BACKUP=$(mktemp)
cp "$TARGET" "$BACKUP"
trap 'cp "$BACKUP" "$TARGET"; rm -f "$BACKUP"' EXIT HUP INT TERM
"$PYTHON" - <<'PY'
from pathlib import Path
p = Path('simulator/battery/waypoints.py')
s = p.read_text()
old = '    if observation is None:\n        return None\n'
assert s.count(old) == 1
p.write_text(s.replace(old, '    if True:\n        return None\n'))
PY
set +e
"$PYTHON" -m pytest tests/battery/test_observation_waypoints.py
STATUS=$?
set -e
test "$STATUS" -eq 1
