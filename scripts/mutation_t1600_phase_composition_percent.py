#!/usr/bin/env python3
"""Prove the phase mol-percent regression test detects a missing /100."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "migrate.py"
TEST = (
    "tests/battery/test_migrate_composition.py::"
    "test_phase_component_mol_percent_scales_exactly_and_preserves_printed"
)
CURRENT = (
    b'def _percent_to_fraction(amount: Decimal) -> Decimal:\n'
    b'    """Convert a recognized percent amount to its dimensionless fraction."""\n'
    b"\n"
    b'    return amount / Decimal("100")\n'
)
MUTANT = CURRENT.replace(
    b'return amount / Decimal("100")', b"return amount", 1
)


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the shared percent scale exactly once")
    SOURCE.write_bytes(original.replace(CURRENT, MUTANT, 1))
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
                "-p",
                "no:cacheprovider",
                "--timeout=300",
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
        raise SystemExit("removing /100 did not fail the phase mol-percent regression")
    print("expected red: removing /100 fails the phase mol-percent regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
