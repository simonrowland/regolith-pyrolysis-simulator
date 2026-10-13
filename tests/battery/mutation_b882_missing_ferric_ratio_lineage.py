"""Mutation proof that the ferric ratio migration test rejects missing lineage."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


mutations = (
    (
        "_migrate_extract_observation",
        "derived_from = derived_from or (choose_read_from(work, locator),)",
        (
            "tests/battery/test_migrate_b686_fe3_fe2_ratio.py::"
            "test_migrated_ferric_fraction_has_resolvable_lineage_and_derivation"
        ),
    ),
    (
        "_emit_exploded_point",
        "derived_from = derived_from or (read_from,)",
        (
            "tests/battery/test_migrate_b686_fe3_fe2_ratio.py::"
            "test_migrator_reduces_ferric_fraction_series_points"
        ),
    ),
)
originals = {
    method_name: getattr(migrate.Migrator, method_name)
    for method_name, _, _ in mutations
}
for method_name, needle, test in mutations:
    for name, original in originals.items():
        setattr(migrate.Migrator, name, original)
    source = textwrap.dedent(inspect.getsource(originals[method_name]))
    assert source.count(needle) == 1
    mutated = source.replace(needle, "derived_from = None")
    namespace: dict[str, object] = {}
    exec(
        compile(mutated, "<b882-missing-ferric-ratio-lineage>", "exec"),
        migrate.__dict__,
        namespace,
    )
    setattr(migrate.Migrator, method_name, namespace[method_name])
    result = pytest.main(
        ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", test]
    )
    print(f"b882 {method_name} MUTATION_EXIT", result)
    assert result == pytest.ExitCode.TESTS_FAILED
