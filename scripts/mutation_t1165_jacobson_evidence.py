#!/usr/bin/env python3
"""Prove the Jacobson evidence regression catches a model-derived revert."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/literature/extracts/kems-139-jacobson-2024.yaml"
TEST = (
    "tests/battery/test_printed_point_conditions.py::"
    "test_jacobson_table1_points_keep_printed_temperature_and_typed_reason"
)
CURRENT = b"method_class: quoted_attributed\n        attribution: Jacobson et al. 2024, Table 1, calculated with FactSage (ref. 115, Bale et al. 2002)"
REVERTED = b"method_class: model_derived\n        attribution: Jacobson et al. 2024, Table 1, calculated with FactSage (ref. 115, Bale et al. 2002)"


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the Jacobson evidence declaration exactly once")
    SOURCE.write_bytes(original.replace(CURRENT, REVERTED, 1))
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
        raise SystemExit(
            "model_derived mutation did not produce only the expected target-test failure"
        )
    print("expected red: restoring model_derived fails the Jacobson regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
