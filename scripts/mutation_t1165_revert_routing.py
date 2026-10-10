#!/usr/bin/env python3
"""Prove the t1165 routing and printed-species regressions detect reverts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "migrate.py"
TEST = (
    "tests/battery/test_printed_point_conditions.py::"
    "test_jacobson_table1_points_keep_printed_temperature_and_typed_reason"
)
CURRENT = (
    b'elif row_point_containers and (\n'
    b'            not _parent_bound_categorical(value_sel)\n'
    b'            or any(name in {"points", "tests"} for name, _items in row_point_containers)\n'
    b'        ):\n'
)
REVERTED = b"elif row_point_containers and not _parent_bound_categorical(value_sel):\n"
SPECIES_CURRENT = b"if use_printed_point_species and printed_species_formula is not None:\n"
SPECIES_REVERTED = b"if False and printed_species_formula is not None:\n"


def _run_mutation(original: bytes, mutated: bytes, label: str) -> None:
    SOURCE.write_bytes(mutated)
    os.utime(SOURCE, None)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "0",
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
        raise SystemExit(
            f"{label} mutation did not produce only the expected target-test failure"
        )
    print(f"expected red: {label} mutation fails the regression test")


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the t1165 routing branch exactly once")
    if original.count(SPECIES_CURRENT) != 1:
        raise SystemExit("could not identify the t1165 species override exactly once")
    _run_mutation(
        original,
        original.replace(CURRENT, REVERTED, 1),
        "point-routing revert",
    )
    _run_mutation(
        original,
        original.replace(SPECIES_CURRENT, SPECIES_REVERTED, 1),
        "printed-species override removal",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
