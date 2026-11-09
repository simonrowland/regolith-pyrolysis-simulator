"""Show that removing the phase_as_analyzed read turns the Kirschen test red.

The helper is the only reader of that field. Restores the file afterwards.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "simulator" / "battery" / "migrate.py"
NEEDLE = (
    "    parts: list[object] = []\n"
    "    for doc in docs:\n"
    "        if isinstance(doc, Mapping):\n"
    '            parts.append(doc.get("phase_as_analyzed"))\n'
    "    return _phase_evidence_text(*parts)\n"
)
REPLACEMENT = "    return None\n"
PYTEST = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-n",
    "0",
    "-p",
    "no:cacheprovider",
    "tests/battery/test_scored_phase_echo.py",
    "-k",
    "test_migrated_kirschen_analysed_glass_keeps_the_stamp",
]


def _run() -> int:
    env = dict(os.environ)
    root = str(ROOT)
    current = env.get("PYTHONPATH", "")
    if root not in current.split(os.pathsep):
        env["PYTHONPATH"] = root if not current else root + os.pathsep + current
    completed = subprocess.run(PYTEST, cwd=ROOT, env=env)
    return completed.returncode


def main() -> int:
    green = _run()
    if green != 0:
        print(f"Kirschen test was already red ({green})", file=sys.stderr)
        return green
    original = TARGET.read_text(encoding="utf-8")
    if NEEDLE not in original:
        print("phase_as_analyzed read not found", file=sys.stderr)
        return 2
    TARGET.write_text(original.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
    try:
        red = _run()
        if red == 0:
            print("mutation did not turn the Kirschen test red", file=sys.stderr)
            return 1
    finally:
        TARGET.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
