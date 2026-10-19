"""Outcome checks shared by focused pytest mutation scripts."""

from __future__ import annotations


def pytest_assertion_failed(
    *,
    returncode: int,
    output: str,
    node_id: str,
    expected_reason: str,
) -> bool:
    """Return whether pytest ran this node and reported its expected failure."""
    lowered_output = output.lower()
    if returncode != 1 or "no tests ran" in lowered_output:
        return False
    if "error collecting" in lowered_output:
        return False

    summary_prefix = f"FAILED {node_id} - "
    return any(
        line.startswith(summary_prefix) and expected_reason in line
        for line in output.splitlines()
    )
