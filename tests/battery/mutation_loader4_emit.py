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
    test_path: str = "tests/battery/test_loader4_emit.py",
    additional_mutations: tuple[tuple[str, str], ...] = (),
    target: object = migrate.Migrator,
) -> None:
    original_method = getattr(target, method_name)
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
    setattr(target, method_name, namespace[method_name])
    try:
        result = pytest.main(
            [
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                f"{test_path}::{test_name}",
            ]
        )
    finally:
        setattr(target, method_name, original_method)
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
    needle='item.get("units")',
    replacement="None",
    test_name="test_real_janaf_tabulated_delta_fg_points_use_the_exploded_buffer",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle='''if emitted_count and (
            not is_tabulated_delta_fg
            or (children_carry_values and all_children_emitted)
        ):''',
    replacement="if emitted_count:",
    test_name="test_janaf_unit_refusal_keeps_the_printed_parent_series",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle='source_unavailable_reason not in (None, "")',
    replacement="False",
    test_name="test_real_nonyield_points_do_not_emit_generic_unavailable_rows",
)
_expect_red(
    method_name="_migrate_extract_observation",
    needle='value_payload.get("unavailable_reason")',
    replacement="None",
    test_name="test_isolated_migration_preserves_species_temperature_conditions_and_scored_values",
    test_path="tests/test_leister_1999_fe_v_redox_2000c_extract.py",
)
_expect_red(
    method_name="_selection_from_named_field",
    needle='''            Quantity.O2_YIELD,
            Quantity.VALENCE_RATIO,
        }:
            bound = _named_fraction_bound_selection(''',
    replacement='''            Quantity.O2_YIELD,
        }:
            bound = _named_fraction_bound_selection(''',
    test_name="test_real_leister_valence_limit_is_stored_as_a_bound",
    target=migrate,
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
