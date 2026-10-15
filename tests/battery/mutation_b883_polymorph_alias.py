"""Prove the extract alias regression catches a missing named-polymorph entry."""

from __future__ import annotations

import pytest

from simulator.battery import migrate


TEST = (
    "tests/battery/test_migrate.py::"
    "test_extract_named_polymorphs_are_formula_scoped"
)


class MutationResult:
    failed: list[str]
    passed: list[str]

    def __init__(self) -> None:
        self.failed = []
        self.passed = []

    def pytest_runtest_logreport(self, report) -> None:
        if TEST not in report.nodeid or report.when != "call":
            return
        if report.failed:
            self.failed.append(report.nodeid)
        elif report.passed:
            self.passed.append(report.nodeid)


def main() -> int:
    aliases = migrate._EXTRACT_POLYMORPH_ALIASES
    mutant = dict(aliases)
    del mutant["high albite"]
    migrate._EXTRACT_POLYMORPH_ALIASES = mutant
    result = MutationResult()
    try:
        exit_code = pytest.main(
            ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", TEST],
            plugins=[result],
        )
    finally:
        migrate._EXTRACT_POLYMORPH_ALIASES = aliases
    assert exit_code == pytest.ExitCode.TESTS_FAILED
    assert len(result.failed) == 1, result.failed
    assert len(result.passed) == 3, result.passed
    assert "high" in result.failed[0].casefold()
    print("expected red: removing the High albite extract alias fails its regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
