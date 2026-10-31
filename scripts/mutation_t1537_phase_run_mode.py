#!/usr/bin/env python3
"""Prove the phase-row path is what passes AlphaMELTS' isothermal mode.

Dropping that call must turn the phase-row run-mode test red. The file
is restored before this process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "score.py"
TEST = (
    "tests/battery/test_phase_row_run_mode.py::"
    "test_phase_row_alphamelts_passes_isothermal_run_mode"
)
CURRENT = (
    b"    kwargs.update(isothermal_run_kwargs(handle.name, parameters))\n"
)
REVERTED = b""


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the phase-row run-mode call exactly once")
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
        raise SystemExit("dropping the phase-row run mode did not fail the test")
    print("expected red: dropping the phase-row run mode fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
