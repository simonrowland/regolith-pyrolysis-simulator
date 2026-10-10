"""Shared acceptance check for mutation-test pytest runs."""

from __future__ import annotations


def is_expected_test_failure(result, test: str) -> bool:
    """Return true only when pytest fails the named test without runner errors."""
    failed_target = any(
        line.startswith(f"FAILED {test}")
        for line in result.stdout.splitlines()
    )
    runner_error = any(
        marker in result.stdout
        for marker in (
            "ERROR collecting",
            "ERROR at setup",
            "ERROR at teardown",
            "INTERNALERROR",
            "Interrupted:",
            "usage: pytest",
        )
    )
    return result.returncode == 1 and failed_target and not runner_error
