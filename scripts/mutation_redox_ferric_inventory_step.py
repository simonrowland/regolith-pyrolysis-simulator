#!/usr/bin/env python3
"""Prove the ferric-inventory stop is what the minute-42 test detects.

Restoring bookable O2 to the full reducing demand brings back the
uncapped Euler step (Robinot minute 42, log10 fO2 to about -19.74).
The file is restored before this process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "core.py"
TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_minute42_reducing_step_stops_at_ferric_inventory"
)
CURRENT = (
    b"            bookable_o2_mol = self._bookable_ferric_o2_mol()\n"
    b"            if demand_o2_mol > bookable_o2_mol + OXYGEN_RESERVOIR_NOOP_MOL:\n"
)
REVERTED = (
    b"            bookable_o2_mol = demand_o2_mol\n"
    b"            if demand_o2_mol > bookable_o2_mol + OXYGEN_RESERVOIR_NOOP_MOL:\n"
)


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit(
            "could not identify the ferric-inventory stop exactly once"
        )
    SOURCE.write_bytes(original.replace(CURRENT, REVERTED, 1))
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
                TEST,
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
    if not is_expected_test_failure(result, TEST):
        raise SystemExit(
            "dropping the ferric-inventory stop did not fail the minute-42 test"
        )
    print("expected red: the reducing tangent walks past the ferric inventory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
