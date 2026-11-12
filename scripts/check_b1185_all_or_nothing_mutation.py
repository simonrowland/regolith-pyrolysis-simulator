#!/usr/bin/env python3
"""Prove the mixed-snapshot glass test detects the old all-or-nothing bug."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


TEST_NODE = "test_silica_qualifies_each_snapshot_and_keeps_exclusions_local"


class _AllOrNothingMutation:
    def __init__(self) -> None:
        self._target: ModuleType | None = None
        self._original: Any = None
        self._failed_call = False

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        if item.name != TEST_NODE:
            return
        self._target = item.module
        self._original = item.module.classify_products
        original = self._original

        def classify_with_old_all_or_nothing(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            silica = result.get("pure_silica_glass", {})
            flag = silica.get("flag", {})
            if (
                silica.get("class_total_kg", 0.0) > 0.0
                and "purity_below_threshold" in str(flag.get("reason", ""))
            ):
                product_kg = float(silica["class_total_kg"])
                silica["class_total_kg"] = 0.0
                glass = result.get("glass", {})
                glass["pure_silica_glass_kg"] = 0.0
                glass["class_total_kg"] = max(
                    float(glass.get("class_total_kg", 0.0)) - product_kg,
                    0.0,
                )
                glass["species_kg"] = {}
            return result

        item.module.classify_products = classify_with_old_all_or_nothing

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.nodeid.endswith(f"::{TEST_NODE}") and report.when == "call":
            self._failed_call = report.failed

    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: int
    ) -> None:
        if self._target is not None:
            self._target.classify_products = self._original


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    test_file = repo_root / "tests" / "test_three_product_report.py"
    sys.path.insert(0, str(repo_root))
    mutation = _AllOrNothingMutation()
    # pyproject addopts enables xdist (-n auto). The monkeypatch lives on this
    # process's plugin object, so workers would execute the unmutated classifier.
    exit_code = pytest.main(
        ["-q", "-n0", f"{test_file}::{TEST_NODE}"],
        plugins=[mutation],
    )
    if exit_code == pytest.ExitCode.TESTS_FAILED and mutation._failed_call:
        print(f"PASS: {TEST_NODE} fails with the all-or-nothing mutation")
        return 0
    print(
        f"FAIL: expected {TEST_NODE} to fail under the mutation; "
        f"pytest exit code={int(exit_code)}, failed_call={mutation._failed_call}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
