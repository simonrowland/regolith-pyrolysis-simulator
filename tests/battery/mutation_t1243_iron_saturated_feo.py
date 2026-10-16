"""Mutation proof that removing the FeO reference conversion turns the tests red."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import score


source = textwrap.dedent(inspect.getsource(score.fusion_comparison_reference))
needle = "iron_saturated = _iron_saturated_feo_comparison(reference, engine=engine)\n"
assert source.count(needle) == 1
mutated = source.replace(needle, "iron_saturated = None\n", 1)
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1243-iron-saturated-feo>", "exec"),
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
        "tests/battery/test_t1243_feo_reference.py",
    ]
)
print("t1243 iron-saturated FeO MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
