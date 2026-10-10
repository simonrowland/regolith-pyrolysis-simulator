"""Mutation proof for rejected successors not displacing measured rows."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate.Migrator._apply_supersedes))
needle = "if new_obs.admission.status not in {"
assert source.count(needle) == 1
mutated = source.replace(needle, "if False and new_obs.admission.status not in {", 1)
namespace: dict[str, object] = {}
exec(
    compile(mutated, "<b771-rejected-superseder-mutation>", "exec"),
    migrate.__dict__,
    namespace,
)
migrate.Migrator._apply_supersedes = namespace["_apply_supersedes"]

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_migrate.py::test_g04_rejected_superseder_does_not_supersede_measured_row",
    ]
)
print("MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
