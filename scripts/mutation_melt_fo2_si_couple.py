#!/usr/bin/env python3
"""Show the post-shuttle Si regression goes red without the Si couple.

Deleting the Si row of ``METAL_SATURATION_FLOOR_OXIDES`` leaves FeO
absent and no other named couple, so dissociation falls through to the
1e-30 bar float guard and P_Si is tens of pascals again. The file is
restored before this process exits. This script shells pytest; the
seat does not run it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "metal_saturation_floor.py"
REGRESSION = (
    "tests/test_metal_saturation_floor.py::"
    "test_post_shuttle_si_pressure_stays_under_pure_si_saturation"
)
# The only Si couple. Fe stays on the wüstite function; Cr, Ti, Mn, and
# Ni stay. The regression composition has none of those oxides.
SI_COUPLE = b'    ("Si", "SiO2"),\n'


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(SI_COUPLE) != 1:
        raise SystemExit("could not identify the Si couple exactly once")
    SOURCE.write_bytes(original.replace(SI_COUPLE, b"", 1))
    os.utime(SOURCE, None)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "--timeout=300",
                "-p",
                "no:cacheprovider",
                REGRESSION,
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        SOURCE.write_bytes(original)
        os.utime(SOURCE, None)
    print(result.stdout, end="")
    if not is_expected_test_failure(result, REGRESSION):
        raise SystemExit(
            "removing the Si couple did not fail the post-shuttle regression"
        )
    print("expected red: P_Si is no longer bounded by the Si couple")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
