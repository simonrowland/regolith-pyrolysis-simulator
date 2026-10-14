"""Restore the activity-path defect in memory and require the regression to fail."""

from __future__ import annotations

import ast
import subprocess

import pytest

from simulator.battery import score


BASE_COMMIT = "186db0088cd6b53177fc242ce858d65f79da9330"
source = subprocess.check_output(
    ["git", "show", f"{BASE_COMMIT}:simulator/battery/score.py"], text=True
)
module = ast.parse(source)
functions = [
    node
    for node in module.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "fusion_comparison_reference"
]
assert len(functions) == 1
namespace: dict[str, object] = {}
exec(
    compile(ast.Module(body=functions, type_ignores=[]), "<t1240-base-scorer>", "exec"),
    score.__dict__,
    namespace,
)
score.fusion_comparison_reference = namespace["fusion_comparison_reference"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_score.py::"
        "test_corrupt_o037_activity_candidate_hash_refuses_compile_residual",
    ]
)
print("t1240 activity candidate integrity MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
