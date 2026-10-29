"""Mutation proof that a failed eligible crystal cannot be skipped."""

from __future__ import annotations

import inspect

import pytest

from simulator.battery.generators import janaf


source = inspect.getsource(janaf._janaf_stable_crystal_set)
needle = '''        except (KeyError, ValueError) as exc:
            raise JANAFFusionPolymorphUnavailable(
                f"{table_id}: stability data unavailable for {oxide} at "
                f"{temperature_K} K: {exc}"
            ) from exc'''
assert source.count(needle) == 1
mutated = source.replace(
    needle,
    "        except (KeyError, ValueError):\n            continue",
    1,
)
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<t1240-skipped-stability-failure>", "exec"),
    janaf.__dict__,
    namespace,
)
janaf._janaf_stable_crystal_set = namespace["_janaf_stable_crystal_set"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_compilation_tier.py::"
        "test_unstated_fusion_polymorph_refuses_incomplete_janaf_candidates",
    ]
)
print("t1240 skipped stability failure MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
