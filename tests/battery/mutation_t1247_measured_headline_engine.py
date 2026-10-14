"""Mutation proof: the measured headline must attribute via ``_engine_of``."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import score


source = textwrap.dedent(inspect.getsource(score._engine_from_payload))
needle = "def _engine_from_payload(row: Mapping[str, object]) -> str:\n"
assert source.count(needle) == 1
mutated = source.replace(needle, needle + '    return "mutated-engine"\n')
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1247-engine-attribution-mutation>", "exec"),
    score.__dict__,
    namespace,
)
score._engine_from_payload = namespace["_engine_from_payload"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_score.py::test_measured_engine_headline_counts_flags_refusals_and_native_rms",
    ]
)
print("MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
