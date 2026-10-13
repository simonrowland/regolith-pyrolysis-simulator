"""Mutation proof that the ferric ratio migration test rejects missing lineage."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate.Migrator._migrate_extract_observation))
needle = "derived_from = derived_from or (choose_read_from(work, locator),)"
assert source.count(needle) == 1
mutated = source.replace(needle, "derived_from = None")
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<b882-missing-ferric-ratio-lineage>", "exec"),
    migrate.__dict__,
    namespace,
)
migrate.Migrator._migrate_extract_observation = namespace["_migrate_extract_observation"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        (
            "tests/battery/test_migrate_b686_fe3_fe2_ratio.py::"
            "test_migrated_ferric_fraction_has_resolvable_lineage_and_derivation"
        ),
    ]
)
print("b882 missing-derived_from MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
