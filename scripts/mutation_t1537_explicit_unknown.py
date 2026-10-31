#!/usr/bin/env python3
"""Prove a printed unknown is what keeps the shared bulk off the row.

Removing the printed-blank guard must turn the regression test red.
The file is restored before this process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "migrate.py"
TEST = (
    "tests/battery/test_equilibrium_starting_bulk.py::"
    "test_explicit_unknown_wins_over_shared_starting_bulk"
)
CURRENT = (
    b"    if _row_printed_composition_unknown(point_conditions):\n"
    b"        return point_conditions, []\n"
)
REVERTED = b""


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the printed-blank guard exactly once")
    mutated = original.replace(CURRENT, REVERTED, 1)
    SOURCE.write_bytes(mutated)
    os.utime(SOURCE, None)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-n", "0", "--timeout=300", TEST],
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
        raise SystemExit("printed-blank revert did not fail the regression test")
    print("expected red: removing the printed-blank guard fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
