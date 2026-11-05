"""Show that deleting the phase-field read turns the Sossi quench test red.

Removes the dedicated ``printed_phase`` read in ``collect_run_texts`` and
restores the file afterwards. The generic provenance loop still skips that
key, so the phase words are not read by another path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "simulator" / "battery" / "input_provenance.py"
NEEDLE = (
    "        printed_phase = provenance.get(\"printed_phase\")\n"
    "        if isinstance(printed_phase, str) and printed_phase.strip():\n"
    "            texts.append((\"observation.phase\", printed_phase.strip()))\n"
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
    "tests/battery/test_scored_phase_echo.py",
    "-k",
    "test_sossi_quench_glass_phase_is_single_melt_evidence",
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
        print(f"Sossi test was already red ({green})", file=sys.stderr)
        return green
    original = TARGET.read_text(encoding="utf-8")
    if NEEDLE not in original:
        print("phase-field read not found", file=sys.stderr)
        return 2
    TARGET.write_text(original.replace(NEEDLE, "", 1), encoding="utf-8")
    try:
        red = _run()
        if red == 0:
            print("mutation did not turn the Sossi test red", file=sys.stderr)
            return 1
    finally:
        TARGET.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
