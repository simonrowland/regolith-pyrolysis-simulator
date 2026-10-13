"""Mutation proof that the ferric ratio migration test rejects missing lineage."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate.fe3_fe2_ratio_parent_lineage))
needle = "return (read_from,)"
assert source.count(needle) == 1
mutated = source.replace(needle, "return derived_from")
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<b882-missing-ferric-ratio-lineage>", "exec"),
    migrate.__dict__,
    namespace,
)
migrate.fe3_fe2_ratio_parent_lineage = namespace["fe3_fe2_ratio_parent_lineage"]
tests = (
    "tests/battery/test_migrate_b686_fe3_fe2_ratio.py::"
    "test_migrated_ferric_fraction_has_resolvable_lineage_and_derivation",
    "tests/battery/test_migrate_b686_fe3_fe2_ratio.py::"
    "test_migrator_reduces_ferric_fraction_series_points",
)
for test in tests:
    result = pytest.main(
        ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", test]
    )
    print("b882 shared-lineage-helper MUTATION_EXIT", test, result)
    assert result == pytest.ExitCode.TESTS_FAILED
