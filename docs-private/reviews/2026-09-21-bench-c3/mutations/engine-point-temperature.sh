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
old = '        point_thermal_gap = None\n'
assert s.count(old) == 1
p.write_text(s.replace(old, '        point_thermal_gap = _thermal_gap(point_thermal)\n'))
PY
set +e
"$PYTHON" -m pytest tests/battery/test_observation_waypoints.py -k engine_point
STATUS=$?
set -e
test "$STATUS" -eq 1
