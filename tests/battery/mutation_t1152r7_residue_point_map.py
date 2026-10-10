"""Mutation proof for carrying the source point map into residue cells."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate.Migrator._emit_exploded_point))
needle = "if source_point_oxide_map is not None"
assert source.count(needle) == 1
mutated = source.replace(needle, "if False")
namespace: dict[str, object] = {}
exec(compile(mutated, "<t1152r7-point-map-mutation>", "exec"), migrate.__dict__, namespace)
migrate.Migrator._emit_exploded_point = namespace["_emit_exploded_point"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_migrate_composition.py::test_markova_residue_uses_its_point_map_without_parent_lookup[tie]",
    ]
)
print("MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
