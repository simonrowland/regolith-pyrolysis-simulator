#!/usr/bin/env python3
"""Prove the Jeon 13-16 reading is the Table 1 ditto flag.

Replacing the ditto convention must turn the extract pin red.
The file is restored before this process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/literature/extracts/jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml"
TEST = (
    "tests/test_jeon_lindberg_2023_mg_ca_zn_phase_equilibria.py::"
    "test_table_row_count_and_table1_condition_disposition"
)
CURRENT = b"table blank = same as above"
REVERTED = b"table blank = printed on this row"
EXPECTED_FLAGS = 166


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != EXPECTED_FLAGS:
        raise SystemExit(
            f"could not identify the ditto convention exactly {EXPECTED_FLAGS} times"
        )
    SOURCE.write_bytes(original.replace(CURRENT, REVERTED))
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
        raise SystemExit("removing the ditto convention did not fail the Jeon pin")
    print("expected red: removing the ditto convention fails the Jeon pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
