"""Mutation proofs for order precedence and supersession-cycle protection."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


source = textwrap.dedent(inspect.getsource(migrate.Migrator._apply_supersedes))


def run_mutation(name: str, mutated: str, test: str) -> None:
    namespace: dict[str, object] = {}
    exec(
        compile(mutated, f"<{name}>", "exec"),
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
            test,
        ]
    )
    print(name, "MUTATION_EXIT", result)
    assert result == pytest.ExitCode.TESTS_FAILED


edge_order = "for old_id, new_id, work_id, locator, source_key in self._pending_supersedes:"
assert source.count(edge_order) == 1
run_mutation(
    "b771-reversed-edge-order-mutation",
    source.replace(
        edge_order,
        edge_order.replace(
            "self._pending_supersedes", "reversed(self._pending_supersedes)"
        ),
    ),
    "tests/battery/test_migrate.py::test_g04_competing_superseders_keep_green_precedence",
)

cycle_guard = "cycle_nodes.update(path[positions[current]:])"
assert source.count(cycle_guard) == 1
run_mutation(
    "b771-removed-cycle-guard-mutation",
    source.replace(cycle_guard, "cycle_nodes.update(())"),
    "tests/battery/test_migrate.py::test_g04_supersedes_cycle_keeps_rows_live_and_emits_hard_issue",
)
