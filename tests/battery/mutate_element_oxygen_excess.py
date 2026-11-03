#!/usr/bin/env python3
"""Break the t-1609 oxygen-demand divisor and require the excess tests to fail.

The production line charges z_i/2 oxygen atoms per cation. This script rewrites
that divisor to z_i/1, runs the hand-arithmetic excess tests, and restores the
source. A green pytest means the tests are not pinned to the formula.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SEAT = Path(__file__).resolve().parents[2]
SOURCE = SEAT / "simulator" / "battery" / "composition.py"
MARKER = (
    "oxygen_demand += cation_moles * Decimal(row.valence) / Decimal(2)"
    "  # t-1609: z_i / 2"
)
BROKEN = (
    "oxygen_demand += cation_moles * Decimal(row.valence) / Decimal(1)"
    "  # t-1609: z_i / 2"
)
PYTEST = (
    "/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python"
)
# The scale-check test uses atomic weights only. It is not a mutation target.
SELECT = (
    "hand_example_relabels or extra_oxygen_mass_fraction "
    "or boundaries_are_all_ferrous or below_zero_and_above "
    "or titanium_manganese"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    scratch = "/private/tmp/claude-501/main-review/w1103t1609"
    env.update(
        {
            "DYLD_FALLBACK_LIBRARY_PATH": "/Users/simonrowland/lib",
            "MPLCONFIGDIR": "/private/tmp/claude-501/main-review/mplcache",
            "OMPI_MCA_orte_tmpdir_base": f"{scratch}/ompi",
            "PRTE_MCA_prte_tmpdir_base": f"{scratch}/ompi",
            "TMPDIR": f"{scratch}/tmp",
            "PYTHONPATH": str(SEAT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    count = original.count(MARKER)
    if count != 1:
        print(f"marker count is {count}, expected 1", file=sys.stderr)
        return 2
    mutated = original.replace(MARKER, BROKEN, 1)
    if mutated == original or BROKEN not in mutated:
        print("replacement did not change the demand line", file=sys.stderr)
        return 2
    SOURCE.write_text(mutated, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                PYTEST,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                "tests/battery/test_element_wt_percent_measured_oxygen.py",
                "-k",
                SELECT,
            ],
            cwd=SEAT,
            env=_env(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    restored = SOURCE.read_text(encoding="utf-8")
    if restored != original or BROKEN in restored or restored.count(MARKER) != 1:
        print("source was not restored", file=sys.stderr)
        return 2
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode == 0 or "FAILED" not in completed.stdout + completed.stderr:
        print(
            "mutation left the excess tests green; the pin does not cover z_i/2",
            file=sys.stderr,
        )
        return 1
    print(
        f"mutation failed the excess tests (pytest exit {completed.returncode}) "
        "and the source was restored"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
