#!/usr/bin/env python3
"""Prove the t1165 point-routing regression fails with the old guard restored."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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


def main() -> int:
    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the t1165 routing branch exactly once")

    SOURCE.write_bytes(original.replace(CURRENT, REVERTED, 1))
    os.utime(SOURCE, None)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-n", "0", TEST],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        SOURCE.write_bytes(original)
        os.utime(SOURCE, None)

    print(result.stdout, end="")
    if result.returncode == 0:
        raise SystemExit("mutation survived: regression test unexpectedly passed")
    print("expected red: reverting point routing fails the regression test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
