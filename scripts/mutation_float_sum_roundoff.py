#!/usr/bin/env python3
"""Restore the fixed absolute slack and prove the focused tests catch it.

This script edits the shared helper only temporarily. It runs the whole light
test files on remote CI and restores the source even when the gate fails.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from _mutation_pytest import is_expected_test_failure


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulator" / "float_tolerance.py"
CURRENT = b"    return max(noop, scaled_slack)\n"
MUTATED = b"    return noop\n"
TEST_FILES = (
    "tests/test_float_tolerance.py",
    "tests/test_vr11_instrumentation.py",
    "tests/test_organics_pyrolysis.py",
    "tests/test_fouling_lifecycle_harness.py",
    "tests/test_redox_ferric_inventory_step.py",
)
EXPECTED_FAILURES = (
    "tests/test_float_tolerance.py",
    "tests/test_vr11_instrumentation.py",
    "tests/test_organics_pyrolysis.py",
    "tests/test_fouling_lifecycle_harness.py",
    "tests/test_redox_ferric_inventory_step.py::test_ferric_handover_guard_scales_the_source_comparison",
    "tests/test_redox_ferric_inventory_step.py::test_ferric_tangent_guard_scales_the_source_comparison",
    "tests/test_redox_ferric_inventory_step.py::test_ferric_lock_reappearance_ignores_two_ulp_inventory_noise",
    "tests/test_redox_ferric_inventory_step.py::test_evaporative_buffer_posting_sum_keeps_roundoff_and_real_source",
    "tests/test_redox_ferric_inventory_step.py::test_cleaned_melt_reduction_sum_keeps_roundoff_and_real_source",
    "tests/test_redox_ferric_inventory_step.py::test_evaporative_metal_loss_sum_keeps_roundoff_and_real_source",
)
REMOTE_CONFIG = (
    "/Users/simonrowland/Repos/regolith-pyrolysis-simulator/"
    ".goal-flight/remote-ci.json"
)


def main() -> int:
    remote = os.environ.get("GOALFLIGHT_REMOTE_RUN")
    if not remote:
        raise SystemExit("GOALFLIGHT_REMOTE_RUN is required")

    original = SOURCE.read_bytes()
    if original.count(CURRENT) != 1:
        raise SystemExit("could not identify the shared scaled-slack return")

    SOURCE.write_bytes(original.replace(CURRENT, MUTATED, 1))
    try:
        result = subprocess.run(
            [
                remote,
                "--config",
                REMOTE_CONFIG,
                "--kind",
                "gate",
                "--cmd-id",
                "regolith-focused-tests",
                "--",
                "python3",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                *TEST_FILES,
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "MPLCONFIGDIR": "/private/tmp/claude-501/main-review/mplcache",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        SOURCE.write_bytes(original)

    print(result.stdout, end="")
    if result.returncode in (69, 75):
        raise SystemExit("remote CI refused or saturated; mutation was restored")
    failed_targets = [
        target
        for target in EXPECTED_FAILURES
        if is_expected_test_failure(result, target)
    ]
    if result.returncode != 1 or len(failed_targets) != len(EXPECTED_FAILURES):
        raise SystemExit(
            "the fixed-slack mutation did not fail every named regression"
        )
    print("expected red: absolute slack trips every named two-ulp regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
