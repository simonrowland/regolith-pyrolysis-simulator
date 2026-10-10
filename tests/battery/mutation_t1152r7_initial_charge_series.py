"""Mutation proof for recognizing flagged initial-charge rows in ``series``."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate._point_rows))
needle = 'for key in ("points", "tests", "series"):'
assert source.count(needle) == 1
mutated = source.replace(needle, 'for key in ("points", "tests"):')
namespace: dict[str, object] = {}
exec(compile(mutated, "<t1152r7-initial-charge-mutation>", "exec"), migrate.__dict__, namespace)
migrate._point_rows = namespace["_point_rows"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_migrate_composition.py::test_markova_table2_migrates_both_quantities_and_printed_charge",
    ]
)
print("MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
