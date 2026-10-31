#!/usr/bin/env python3
"""Prove the equilibrium starting-bulk rule is what the new tests detect.

Reverting the call that copies the printed starting bulk onto an
equilibrium phase row must turn those tests red. The file is restored
before this process exits.
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
    "test_closed_starting_bulk_becomes_identity_not_the_glass"
)
CURRENT = (
    b"            point_conditions, starting_bulk_notices = (\n"
    b"                _apply_equilibrium_starting_bulk(\n"
    b"                    experiment,\n"
    b"                    ident_kwargs,\n"
    b"                    point_conditions,\n"
    b"                    origin=obs_id,\n"
    b"                )\n"
    b"            )\n"
)
REVERTED = (
    b"            point_conditions, starting_bulk_notices = (\n"
    b"                point_conditions,\n"
    b"                [],\n"
    b"            )\n"
)


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the starting-bulk call exactly once")
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
        raise SystemExit("starting-bulk revert did not fail the closed-system test")
    print("expected red: reverting the starting-bulk call fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
