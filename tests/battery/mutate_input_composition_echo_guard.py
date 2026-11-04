"""Show that deleting the echo guard turns the Bonnand major-oxide test red.

Inserts ``return None`` as the first line of ``input_composition_echo_detail``
and restores the file afterwards. Run from a checkout with the seat on
PYTHONPATH. No absolute paths.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "simulator" / "battery" / "input_provenance.py"
NEEDLE = (
    "def input_composition_echo_detail(identity: Identity) "
    "-> dict[str, object] | None:\n"
)
INSERT = NEEDLE + "    return None\n"
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
    "test_migrated_borisov_2004_row_refuses_the_echo",
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
        print(f"Bonnand test was already red ({green})", file=sys.stderr)
        return green
    original = TARGET.read_text(encoding="utf-8")
    if NEEDLE not in original:
        print("guard function not found", file=sys.stderr)
        return 2
    TARGET.write_text(original.replace(NEEDLE, INSERT, 1), encoding="utf-8")
    try:
        red = _run()
        if red == 0:
            print("mutation did not turn the Bonnand test red", file=sys.stderr)
            return 1
    finally:
        TARGET.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
