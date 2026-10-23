"""Prove that the full OpenIMCC contract file detects lost extrapolation flags.

Run from the repository root with the seat on PYTHONPATH and MPLCONFIGDIR set:
``python tests/battery/mutation_t1263_openimcc_extrapolation.py``.
"""

from __future__ import annotations

import copy
import multiprocessing
import re
from typing import Callable

import pytest


_TEST_FILE = "tests/test_openimcc_battery_engine.py"
_EXPECTED_FAILURE = (
    "tests/test_openimcc_battery_engine.py::"
    "test_openimcc_below_floor_real_melt_activity_has_extrapolation_flag"
)


class _FailureWitness:
    def __init__(self) -> None:
        self.failed: set[str] = set()

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call" and report.failed:
            self.failed.add(report.nodeid)


def _mutate_engine_temperature_notice(mutation: str) -> Callable[[], None]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )

    original = _OpenImccBatteryBackend.equilibrate

    def mutated(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        diagnostics = copy.deepcopy(result.diagnostics)
        notices = diagnostics["imcc_notices"]
        if mutation == "strip flag text":
            diagnostics["imcc_notices"] = [
                notice
                for notice in notices
                if notice.get("kind") != "openimcc_temperature_extrapolated"
            ]
        elif mutation == "strip band":
            for notice in notices:
                if notice.get("kind") != "openimcc_temperature_extrapolated":
                    continue
                reason, count = re.subn(
                    r"certified band \[[^\]]+\] K",
                    "certified band unavailable",
                    str(notice["reason"]),
                    count=1,
                )
                if count != 1:
                    raise AssertionError("temperature flag did not contain its band")
                notice["reason"] = reason
                notice.pop("certified_band", None)
        result.diagnostics = diagnostics
        return result

    _OpenImccBatteryBackend.equilibrate = mutated

    def restore() -> None:
        _OpenImccBatteryBackend.equilibrate = original

    return restore


def _mutate_refuse_extrapolation() -> Callable[[], None]:
    from simulator.melt_backend import openimcc_bridge

    original = openimcc_bridge.evaluate

    def refuse_temperature_extrapolation(**kwargs):
        kwargs["allow_extrapolation"] = False
        return original(**kwargs)

    openimcc_bridge.evaluate = refuse_temperature_extrapolation

    def restore() -> None:
        openimcc_bridge.evaluate = original

    return restore


def _run_whole_file_mutation(
    name: str, install: Callable[[], Callable[[], None]]
) -> None:
    restore = install()
    witness = _FailureWitness()
    try:
        exit_code = pytest.main(
            ["-q", "-n", "0", "--timeout=300", _TEST_FILE],
            plugins=[witness],
        )
    finally:
        restore()

    if _EXPECTED_FAILURE not in witness.failed or int(exit_code) == 0:
        raise SystemExit(
            f"{name} mutation did not make {_EXPECTED_FAILURE} fail; "
            f"failed tests: {sorted(witness.failed)}"
        )
    print(f"{name} mutation verified red: {_EXPECTED_FAILURE}")


def main() -> None:
    for name, install in (
        (
            "strip flag text",
            lambda: _mutate_engine_temperature_notice("strip flag text"),
        ),
        ("strip band", lambda: _mutate_engine_temperature_notice("strip band")),
        ("refuse extrapolation", _mutate_refuse_extrapolation),
    ):
        _run_whole_file_mutation(name, install)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
