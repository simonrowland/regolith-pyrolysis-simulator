#!/usr/bin/env python3
"""Prove the ferric-inventory tests detect the two stops they name.

Restoring bookable O2 to the full reducing demand brings back the
uncapped Euler step (Robinot minute 42, log10 fO2 to about -19.74).
Restoring the clear-on-positive-source drops the ferric lock when the
same hour's metal-loss credit arrives, which the defect-hour test
rejects. The file is restored before this process exits, and again
between the two mutations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "core.py"
INVENTORY_TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_minute42_reducing_step_stops_at_ferric_inventory"
)
DEFECT_TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_defect_hour_metal_loss_keeps_the_ferric_lock"
)
INVENTORY_CURRENT = (
    b"            bookable_o2_mol = self._bookable_ferric_o2_mol()\n"
    b"            if demand_o2_mol > bookable_o2_mol + OXYGEN_RESERVOIR_NOOP_MOL:\n"
)
INVENTORY_REVERTED = (
    b"            bookable_o2_mol = demand_o2_mol\n"
    b"            if demand_o2_mol > bookable_o2_mol + OXYGEN_RESERVOIR_NOOP_MOL:\n"
)
# The source-term republish, and only that call: the exchange site is
# followed by _write_ferric_tangent_reasons, not by `if terms:`.
CLEAR_ABSENT = (
    b"        self._republish_while_ferric_lock_stands(\n"
    b"            reservoir,\n"
    b"            T_K=T_K,\n"
    b"            base_fO2_log=base_fO2_log,\n"
    b"        )\n"
    b"        if terms:\n"
)
CLEAR_RESTORED = (
    b"        self._republish_while_ferric_lock_stands(\n"
    b"            reservoir,\n"
    b"            T_K=T_K,\n"
    b"            base_fO2_log=base_fO2_log,\n"
    b"        )\n"
    b"        if (\n"
    b"            net_o2_equiv_mol > OXYGEN_RESERVOIR_NOOP_MOL\n"
    b"            and not skip_reason\n"
    b"        ):\n"
    b"            self._clear_ferric_tangent_exhaustion()\n"
    b"        if terms:\n"
)


def _expect_red(
    original: bytes,
    current: bytes,
    reverted: bytes,
    test: str,
    missing: str,
    still_green: str,
    caught: str,
) -> None:
    if original.count(current) != 1:
        raise SystemExit(missing)
    SOURCE.write_bytes(original.replace(current, reverted, 1))
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
                "--timeout=300",
                "-p",
                "no:cacheprovider",
                test,
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
    if not is_expected_test_failure(result, test):
        raise SystemExit(still_green)
    print(caught)


def main() -> int:
    original = SOURCE.read_bytes()
    try:
        _expect_red(
            original,
            INVENTORY_CURRENT,
            INVENTORY_REVERTED,
            INVENTORY_TEST,
            "could not identify the ferric-inventory stop exactly once",
            "dropping the ferric-inventory stop did not fail the minute-42 test",
            "expected red: the reducing tangent walks past the ferric inventory",
        )
        _expect_red(
            original,
            CLEAR_ABSENT,
            CLEAR_RESTORED,
            DEFECT_TEST,
            "could not identify the source-term republish exactly once",
            "restoring the clear-on-positive-source did not fail the defect-hour test",
            "expected red: a positive source clears the ferric lock",
        )
    finally:
        SOURCE.write_bytes(original)
        os.utime(SOURCE, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
