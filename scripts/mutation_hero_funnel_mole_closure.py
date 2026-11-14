#!/usr/bin/env python3
"""Prove the mole-fraction closure check is what the refusal test detects.

Deleting the 1 ± 0.02 check must turn the unclosed-set test red. An
unclosed Babaian-shaped set would then be converted. The file is restored
before this process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "battery" / "composition.py"
TEST = (
    "tests/battery/test_phase_composition_rows.py::"
    "test_unclosed_mole_fraction_set_stays_refused"
)
CURRENT = (
    b"    # Closure is on the mole fractions. The mass sum is not a renormaliser.\n"
    b"    if abs(total - Decimal(1)) > _MOLE_FRACTION_CLOSURE:\n"
    b'        return None, "phase_mole_fraction_set_unclosed"\n'
)
REVERTED = b"    # mutation: the closure check is gone\n"


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the mole-fraction closure check exactly once")
    SOURCE.write_bytes(original.replace(CURRENT, REVERTED, 1))
    os.utime(SOURCE, None)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-n", "0", "--timeout=300", "-p", "no:cacheprovider", TEST],
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
        raise SystemExit("dropping the closure check did not fail the unclosed-set test")
    print("expected red: an unclosed mole-fraction set is no longer refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
