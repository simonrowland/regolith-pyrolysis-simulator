"""Mutation proof that omitting the FeO flagged stratum turns the probe red."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import score


source = textwrap.dedent(inspect.getsource(score._classify_flagged_notice_facts))
needle = "        strata.append(FLAGGED_STRATUM_SOURCE_FLAGGED_LESS_RELIABLE)\n"
assert source.count(needle) == 1
mutated = source.replace(needle, "        pass\n", 1)
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1243-iron-saturated-feo>", "exec"),
    score.__dict__,
    namespace,
)
score._classify_flagged_notice_facts = namespace["_classify_flagged_notice_facts"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_t1243_feo_reference.py::test_complete_measured_direct_feo_probe_is_excluded_from_headline",
    ]
)
print("t1243 iron-saturated FeO stratum MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
