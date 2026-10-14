"""Mutation proof that an unstated fusion polymorph cannot score silently."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import compilation_tier


source = textwrap.dedent(inspect.getsource(compilation_tier.predict_thermo_attempt))
needle = "if polymorph_unspecified:\n            alternatives = tuple("
assert source.count(needle) == 1
mutated = source.replace(
    needle, "if False:\n            alternatives = tuple(", 1
)
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1240-silent-fusion-polymorph>", "exec"),
    compilation_tier.__dict__,
    namespace,
)
compilation_tier.predict_thermo_attempt = namespace["predict_thermo_attempt"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_compilation_tier.py::"
        "test_fusion_prediction_flags_an_unstated_solid_polymorph",
    ]
)
print("t1240 silent fusion polymorph MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
