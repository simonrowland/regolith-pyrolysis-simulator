#!/usr/bin/env python3
"""Prove the ferric-inventory tests detect the stops they name.

Restoring the uncapped Euler in place of the ferric booking brings
back Robinot minute 42 (log10 fO2 to about -19.74). Restoring the
clear-on-positive-source drops the ferric lock when the same hour's
metal-loss credit arrives. Skipping the Fe-split release leaves the
equilibrium q, so minute 43 stays on the ceiling. Writing the split
override publishes that ceiling on a dust settle. Hardcoding a_FeO = 1
on the floor or on the native onset splits the two apart.

The file is restored before this process exits, and again between
mutations. This script shells pytest; the seat does not run it.
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
EQUILIBRIUM_TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_defect_hour_equilibrium_q_books_ferric_and_minute_43_reads_the_floor"
)
DUST_TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_dust_oxidizing_settle_publishes_ledger_not_the_ceiling"
)
ONSET_TEST = (
    "tests/test_redox_ferric_inventory_step.py::"
    "test_floor_and_native_onset_share_melt_activity"
)
# Skipping the booking and publishing the uncapped Euler
# (base + net/C_m/ln(10)) is the minute-42 crash, about -19.74.
INVENTORY_CURRENT = (
    b"            self._release_ledger_ferric_toward_fO2(\n"
    b"                target_fO2_log,\n"
    b"                T_K=T_K,\n"
    b"            )\n"
    b"            published_fO2_log = self._ledger_consistent_melt_fO2_log(T_K)\n"
)
INVENTORY_REVERTED = (
    b"            published_fO2_log = base_fO2_log + (\n"
    b"                net_o2_equiv_mol / C_m\n"
    b"            ) / math.log(10.0)\n"
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
# Drop the release and still publish the pre-booking ledger. q stays
# at the ceiling equilibrium, so minute 43 reads -1.886.
RELEASE_CURRENT = (
    b"            self._release_ledger_ferric_toward_fO2(\n"
    b"                target_fO2_log,\n"
    b"                T_K=T_K,\n"
    b"            )\n"
)
RELEASE_REVERTED = b"            pass\n"
# Always write the split target. A partial dust settle then publishes
# the effervescence ceiling instead of the post-split ledger.
OVERRIDE_CURRENT = (
    b"            published_fO2_log = self._ledger_consistent_melt_fO2_log(T_K)\n"
    b"            reservoir.melt_intrinsic_fO2_log = (\n"
    b"                self._finite_oxygen_reservoir_fO2_log(\n"
    b"                    published_fO2_log,\n"
    b"                    context='fe_redox_respeciation',\n"
)
OVERRIDE_REVERTED = (
    b"            published_fO2_log = fO2_log\n"
    b"            reservoir.melt_intrinsic_fO2_log = (\n"
    b"                self._finite_oxygen_reservoir_fO2_log(\n"
    b"                    published_fO2_log,\n"
    b"                    context='fe_redox_respeciation',\n"
)
# Floor hardcoded at a_stoich = 1. The pure line is more oxidizing
# than the melt activity, so native Fe is still zero a step below it.
FLOOR_CURRENT = b"                a_feo_stoich=activity,\n"
FLOOR_REVERTED = b"                a_feo_stoich=1.0,\n"
# Native onset restored to a_stoich = 1 while the floor uses the melt
# activity. A step above the floor is still below that onset.
NATIVE_CURRENT = b"            a_feo_stoich=a_feo,\n"
NATIVE_REVERTED = b"            a_feo_stoich=1.0,\n"


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
            "could not identify the ferric booking exactly once",
            "publishing the uncapped Euler did not fail the minute-42 test",
            "expected red: the reducing step walks past the ferric inventory",
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
        _expect_red(
            original,
            RELEASE_CURRENT,
            RELEASE_REVERTED,
            EQUILIBRIUM_TEST,
            "could not identify the ferric release exactly once",
            "skipping the ferric release did not fail the equilibrium-q test",
            "expected red: minute 43 stays on the ceiling",
        )
        _expect_red(
            original,
            OVERRIDE_CURRENT,
            OVERRIDE_REVERTED,
            DUST_TEST,
            "could not identify the committed-split ledger publish exactly once",
            "writing the split override did not fail the dust-settle test",
            "expected red: a partial settle publishes the ceiling",
        )
        _expect_red(
            original,
            FLOOR_CURRENT,
            FLOOR_REVERTED,
            ONSET_TEST,
            "could not identify the melt-activity floor exactly once",
            "hardcoding the floor at a_FeO = 1 did not fail the onset test",
            "expected red: the pure-line floor and the native onset disagree",
        )
        _expect_red(
            original,
            NATIVE_CURRENT,
            NATIVE_REVERTED,
            ONSET_TEST,
            "could not identify the native onset activity exactly once",
            "hardcoding native onset at a_FeO = 1 did not fail the onset test",
            "expected red: native Fe is still saturated above the melt floor",
        )
    finally:
        SOURCE.write_bytes(original)
        os.utime(SOURCE, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
