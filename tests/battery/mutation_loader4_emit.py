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
) -> None:
    original_method = getattr(migrate.Migrator, method_name)
    source = textwrap.dedent(inspect.getsource(original_method))
    assert source.count(needle) == 1, (method_name, needle, source.count(needle))
    namespace: dict[str, object] = {}
    mutated = source.replace(needle, replacement)
    exec(
        compile(mutated, f"<loader4-{method_name}-mutation>", "exec"),
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
