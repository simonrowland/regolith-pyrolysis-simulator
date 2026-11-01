#!/usr/bin/env python3
"""Prove the anhydrous-bulk notice is what the new test detects.

Dropping that notice must turn the regression test red. The file is
restored before this process exits.
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
    "test_anhydrous_note_flags_omitted_water"
)
CURRENT = (
    b"    if _printed_anhydrous_normalization(initial.locator):\n"
    b"        notices.append(\n"
    b"            Notice(\n"
    b"                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,\n"
    b"                affected_quantities=(Quantity.EQUILIBRIUM_PHASE_COMPOSITION,),\n"
    b"                reason=(\n"
    b'                    "anhydrous_starting_bulk_omits_h2o: "\n'
    b'                    "identity.composition omits H2O; the printed starting "\n'
    b'                    "vector is normalized to 100% anhydrous"\n'
    b"                ),\n"
    b"                origin=origin,\n"
    b"                band=_ANHYDROUS_STARTING_BULK_BAND,\n"
    b"                authority=Authority.EXTRAPOLATED,\n"
    b"            )\n"
    b"        )\n"
)
REVERTED = b""


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the anhydrous notice exactly once")
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
        raise SystemExit("dropping the anhydrous notice did not fail the test")
    print("expected red: dropping the anhydrous notice fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
