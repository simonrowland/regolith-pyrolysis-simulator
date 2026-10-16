"""Prove that refusing below-floor extrapolation breaks the full contract file.

Run from the repository root with the seat on PYTHONPATH and MPLCONFIGDIR set:
``python tests/battery/mutation_t1263_openimcc_extrapolation.py``.
"""

from __future__ import annotations

import multiprocessing

import pytest


class _FailureWitness:
    def __init__(self) -> None:
        self.failed: set[str] = set()

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call" and report.failed:
            self.failed.add(report.nodeid)


def main() -> None:
    from simulator.melt_backend import openimcc_bridge

    original_evaluate = openimcc_bridge.evaluate

    def refuse_temperature_extrapolation(**kwargs):
        kwargs["allow_extrapolation"] = False
        return original_evaluate(**kwargs)

    openimcc_bridge.evaluate = refuse_temperature_extrapolation
    witness = _FailureWitness()
    try:
        exit_code = pytest.main(
            [
                "-q",
                "-n",
                "0",
                "--timeout=300",
                "tests/test_openimcc_battery_engine.py",
            ],
            plugins=[witness],
        )
    finally:
        openimcc_bridge.evaluate = original_evaluate

    expected_failure = (
        "tests/test_openimcc_battery_engine.py::"
        "test_openimcc_below_floor_real_melt_activity_has_extrapolation_flag"
    )
    if expected_failure not in witness.failed:
        raise SystemExit(
            "mutation did not make the focused below-floor test fail; "
            f"failed tests: {sorted(witness.failed)}"
        )
    if int(exit_code) == 0:
        raise SystemExit("mutation returned a passing pytest status")
    print(f"mutation verified: {expected_failure} failed as expected")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
