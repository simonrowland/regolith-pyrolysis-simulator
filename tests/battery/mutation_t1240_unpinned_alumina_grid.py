"""Mutation proof that an added alumina grid pin detects a deleted row."""

from __future__ import annotations

import pytest

from simulator.battery.generators import janaf


janaf._FUSION_NODE_SET_SHA256.pop("Al-097")

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_compilation_tier.py::"
        "test_missing_nonlocal_alumina_candidate_node_refuses_stability_selection",
    ]
)
print("t1240 unpinned alumina grid MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
