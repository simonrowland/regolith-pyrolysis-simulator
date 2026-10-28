"""Mutation checks for the loader4 exploded-point regressions."""

from __future__ import annotations

import inspect
import textwrap

import pytest

from simulator.battery import migrate


def _expect_red(
    *,
    method_name: str,
    needle: str,
    replacement: str,
    test_name: str,
    additional_mutations: tuple[tuple[str, str], ...] = (),
) -> None:
    original_method = getattr(migrate.Migrator, method_name)
    source = textwrap.dedent(inspect.getsource(original_method))
    mutations = ((needle, replacement), *additional_mutations)
    for mutation_needle, _mutation_replacement in mutations:
        assert source.count(mutation_needle) == 1, (
            method_name,
            mutation_needle,
            source.count(mutation_needle),
        )
    for mutation_needle, mutation_replacement in mutations:
        source = source.replace(mutation_needle, mutation_replacement)
    namespace: dict[str, object] = {}
    exec(
        compile(source, f"<loader4-{method_name}-mutation>", "exec"),
        migrate.__dict__,
        namespace,
    )
    setattr(migrate.Migrator, method_name, namespace[method_name])
    try:
        result = pytest.main(
            [
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                f"tests/battery/test_loader4_emit.py::{test_name}",
            ]
        )
    finally:
        setattr(migrate.Migrator, method_name, original_method)
    print(f"MUTATION_EXIT {test_name} {result}")
    assert result == pytest.ExitCode.TESTS_FAILED


_expect_red(
    method_name="_emit_exploded_point",
    needle="**explicit_point_conditions",
    replacement="**{}",
    test_name="test_real_point_conditions_override_inferred_conditions",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle="or isinstance(values.get(\"tabulated_delta_fG_kJ_mol\"), list)",
    replacement="or False",
    test_name="test_real_janaf_tabulated_delta_fg_points_use_the_exploded_buffer",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle='and raw.get("omitted_reason") not in (None, "")',
    replacement="and True",
    test_name="test_real_nonyield_points_emit_unavailable_rows_with_conditions",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle="not _parent_bound_categorical(value_sel)\n        or any(name in",
    replacement="True\n        or any(name in",
    test_name="test_real_rows_that_fail_series_admission_stay_as_parent_context",
    additional_mutations=(
        (
            "if _parent_bound_categorical(value_sel) and not non_yield_points:",
            "if False:",
        ),
    ),
)
