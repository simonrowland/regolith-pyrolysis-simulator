from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import load_migrated_store, migrate
from simulator.battery.records import RUN_METADATA_FIELD_HOMES, Value
from tests.battery.test_migrate import _write_min_tree
from tests.battery.test_run_metadata_schema import (
    _RUN_METADATA_UNIT_KEYS,
    _loaded_run_metadata_inference,
    _loaded_run_metadata_value,
    _run_metadata_field_payload,
    _run_metadata_mapping_at,
    _run_metadata_mapping_paths,
    _run_metadata_shape_extract,
    _unitless_run_metadata_extract,
)


def test_run_metadata_unit_keys_are_consumed_or_typed_at_every_home(
    tmp_path: Path,
) -> None:
    mutations = 0
    fields = (
        "mass_kg",
        "final_mass_kg",
        "species_loss_fraction",
        "charge_sequence",
        "vaporization_statement",
    )
    located_value_shapes = (
        "scalar",
        "point",
        "located-state",
        "series",
        "interval",
        "bound",
        "inference-with-parameters",
        "wrapped-inference",
        "inner-inference",
        "as-published-point",
        "as-published-interval",
        "nested-combination",
        "misplaced-as-published",
    )
    shapes_by_field = {
        "mass_kg": located_value_shapes,
        "final_mass_kg": located_value_shapes,
        "species_loss_fraction": (
            "scalar",
            "point",
            "located-state",
            "series",
            "interval",
            "bound",
            "inference-with-parameters",
        ),
        "charge_sequence": ("unitless", "unitless-with-inference"),
        "vaporization_statement": ("unitless", "unitless-with-inference"),
    }
    cases = [
        (field, home, shape)
        for field in fields
        for home in RUN_METADATA_FIELD_HOMES[field][1]
        for shape in shapes_by_field[field]
    ]
    priority_cases = [
        case
        for case in cases
        if (
            case[0] in {"mass_kg", "final_mass_kg"}
            and case[2] in {"wrapped-inference", "inner-inference"}
        )
        or (
            case[0] in {"charge_sequence", "vaporization_statement"}
            and case[2] == "unitless-with-inference"
        )
    ]
    priority_case_set = set(priority_cases)
    ordered_cases = priority_cases + [
        case for case in cases if case not in priority_case_set
    ]
    for seeds, (field, home, shape) in enumerate(ordered_cases):
        seed_root = tmp_path / f"seed-{seeds}"
        if shape.startswith("unitless"):
            payload = (
                {
                    "charge_id": "charge-a",
                    "run_index": 1,
                    "locator": {"table": "unit-matrix", "page": 1},
                }
                if field == "charge_sequence"
                else {
                    "state": {"tag": "value", "value": "congruent"},
                    "locator": {"table": "unit-matrix", "page": 1},
                }
            )
            if shape == "unitless-with-inference":
                payload["inference"] = {
                    "method": "fixture",
                    "relation": "identity",
                    "inputs": ["source"],
                    "output_unit": "g",
                }
            _seed, base_extract = _unitless_run_metadata_extract(
                seed_root, field, home, payload
            )
        else:
            _seed, base_extract = _run_metadata_shape_extract(
                seed_root, field, home, shape
            )

        base_payload = _run_metadata_field_payload(base_extract, field, home)
        for node_path in _run_metadata_mapping_paths(base_payload):
            for unit_key in _RUN_METADATA_UNIT_KEYS:
                extract = deepcopy(base_extract)
                field_payload = _run_metadata_field_payload(extract, field, home)
                _run_metadata_mapping_at(field_payload, node_path)[unit_key] = "g"
                root = _write_min_tree(
                    tmp_path / f"mutation-{mutations}", extract
                )
                result = migrate(root, write=True)
                assert result.validation is not None
                experiment_id = "10.1234/fixture::experiment::run-1"
                _works, experiments, _observations = load_migrated_store(root)
                loaded = _loaded_run_metadata_value(
                    experiments[experiment_id], field
                )
                field_issues = [
                    issue
                    for issue in result.validation.issues
                    if issue.reason is RefusalReason.INVALID_SOURCE
                    and field in issue.path
                ]
                context = (
                    f"field={field}, home={home}, shape={shape}, "
                    f"node={node_path}, key={unit_key}"
                )
                if field_issues:
                    if field == "species_loss_fraction" and node_path == ():
                        assert any(
                            issue.path.endswith(f"[{unit_key}]")
                            for issue in field_issues
                        ), context
                        assert loaded is None or unit_key not in loaded, context
                    else:
                        assert loaded is None, context
                    if shape == "misplaced-as-published":
                        assert any(
                            "as_published is only valid on the Located value"
                            in issue.detail
                            for issue in field_issues
                        ), context
                elif unit_key.casefold() == "output_unit":
                    inference = _loaded_run_metadata_inference(
                        experiments[experiment_id], field
                    )
                    assert inference is not None, context
                    assert inference.output_unit == "g", context
                elif (
                    field in {"mass_kg", "final_mass_kg"}
                    and loaded is not None
                    and loaded.inference is not None
                ):
                    assert loaded.state.is_value, context
                    assert loaded.inference.relation == "g_to_kg", context
                    assert loaded.inference.output_unit == "kg", context
                elif (
                    field in {"mass_kg", "final_mass_kg"}
                    and unit_key in {"unit", "units"}
                ):
                    assert loaded is not None and loaded.state.is_value, context
                    printed = Decimal(
                        "0.0008" if field == "final_mass_kg" else "0.25"
                    )
                    assert loaded.state.value == Value.point_of(
                        printed / Decimal(1000)
                    ), context
                else:
                    pytest.fail(
                        "unit-like key was accepted without a consuming reader: "
                        f"{context}"
                    )
                mutations += 1
