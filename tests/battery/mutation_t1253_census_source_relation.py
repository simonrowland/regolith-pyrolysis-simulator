"""Mutation proof that the census regression catches lost coefficient lineage."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import compilation_tier


source = textwrap.dedent(inspect.getsource(compilation_tier._prediction_from_attempt))
needle = """coefficient_sources=_attempt_coefficient_sources(
            engine, attempt.coefficient_sources
        ),"""
assert source.count(needle) == 1
mutated = source.replace(needle, "coefficient_sources=(),")
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1253-empty-coefficient-lineage>", "exec"),
    compilation_tier.__dict__,
    namespace,
)
compilation_tier._prediction_from_attempt = namespace["_prediction_from_attempt"]

test = (
    "tests/battery/test_compilation_tier.py::"
    "test_vaporock_gas_census_preserves_same_input_relation"
)
result = pytest.main(
    ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", test]
)
print("t1253 coefficient-lineage mutation exit", result)
assert result == pytest.ExitCode.TESTS_FAILED
