#!/usr/bin/env python3
"""Prove t-1639 catches sample labels being parsed as different species."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "migrate.py"
TEST = (
    "tests/battery/test_migrate.py::"
    "test_point_sample_label_different_from_printed_species_never_infers"
)
CURRENT = (
    b"    if not label or species_as_printed is not None or label != parent:\n"
    b"        return None\n"
)
REVERTED = b"    if not label:\n        return None\n"


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the t1639 sample-label rule exactly once")
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
            timeout=300,
        )
    finally:
        SOURCE.write_bytes(original)
        os.utime(SOURCE, None)
    print(result.stdout, end="")
    if not is_expected_test_failure(result, TEST):
        raise SystemExit("sample-label inference revert did not fail only the target test")
    print("expected red: restoring label formula inference fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
