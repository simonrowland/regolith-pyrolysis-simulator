"""Restore the old exploded-child phase call and prove the Kress pin fails."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "simulator" / "battery" / "migrate.py"
NEEDLE = (
    "            phase_text=(\n"
    "                row_phase_text if row_has_phase_evidence else parent_phase_text\n"
    "            ),\n"
    "            analysed_phase_text=(\n"
    "                row_analysed_phase_text\n"
    "                if row_has_phase_evidence\n"
    "                else parent_analysed_phase_text\n"
    "            ),\n"
)
REPLACEMENT = (
    "            phase_text=row_phase_text,\n"
    "            analysed_phase_text=_analysed_phase_text(raw_item, parent_values),\n"
)
PYTEST = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-n",
    "0",
    "-p",
    "no:cacheprovider",
    "tests/battery/test_migrate_redox_row_projection.py",
    "-k",
    "kress_table1_redox_rows_project_printed_conditions",
]


def _run() -> int:
    env = dict(os.environ)
    root = str(ROOT)
    current = env.get("PYTHONPATH", "")
    if root not in current.split(os.pathsep):
        env["PYTHONPATH"] = root if not current else root + os.pathsep + current
    return subprocess.run(PYTEST, cwd=ROOT, env=env).returncode


def main() -> int:
    if _run() != 0:
        print("Kress parent-inherit test was already red", file=sys.stderr)
        return 1
    original = TARGET.read_text(encoding="utf-8")
    if NEEDLE not in original:
        print("parent phase fallback not found", file=sys.stderr)
        return 2
    TARGET.write_text(original.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
    try:
        if _run() == 0:
            print("mutation did not turn the Kress test red", file=sys.stderr)
            return 1
    finally:
        TARGET.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
