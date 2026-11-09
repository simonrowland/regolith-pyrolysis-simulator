from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.migrate import _parse_run_metadata_shape
from simulator.battery.records import RUN_METADATA_FIELD_HOMES, Value
from tests.battery.test_run_metadata_schema import (
    _RUN_METADATA_UNIT_KEYS,
    _run_metadata_field_payload,
    _run_metadata_mapping_at,
    _run_metadata_mapping_paths,
    _run_metadata_shape_extract,
    _unitless_run_metadata_extract,
)


def test_run_metadata_unit_keys_are_consumed_or_typed_at_every_home(
    tmp_path: Path,
) -> None:
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
                }
            _seed, base_extract = _unitless_run_metadata_extract(
                seed_root, field, home, payload
            )
        else:
            _seed, base_extract = _run_metadata_shape_extract(
                seed_root, field, home, shape
            )

        base_payload = _run_metadata_field_payload(base_extract, field, home)
        if shape == "unitless-with-inference":
            baseline, baseline_errors = _parse_run_metadata_shape(
                RUN_METADATA_FIELD_HOMES[field][0],
                base_payload,
                field=field,
            )
            if field == "charge_sequence":
                assert baseline is None, (field, home, baseline)
                assert any(
                    "inference" in detail
                    for _formula, _raw, detail in baseline_errors
                )
            else:
                assert baseline_errors == (), (field, home, baseline_errors)
                assert baseline.inference is not None, (field, home, baseline)

        for node_path in _run_metadata_mapping_paths(base_payload):
            for unit_key in _RUN_METADATA_UNIT_KEYS:
                extract = deepcopy(base_extract)
                field_payload = _run_metadata_field_payload(extract, field, home)
                _run_metadata_mapping_at(field_payload, node_path)[unit_key] = "g"
                parsed, errors = _parse_run_metadata_shape(
                    RUN_METADATA_FIELD_HOMES[field][0],
                    field_payload,
                    field=field,
                )
                context = (
                    f"field={field}, home={home}, shape={shape}, "
                    f"node={node_path}, key={unit_key}"
                )
                if errors:
                    if field == "species_loss_fraction" and parsed is not None:
                        assert unit_key not in parsed, context
                        assert any(
                            formula == unit_key
                            for formula, _raw_value, _detail in errors
                        ), context
                    else:
                        assert parsed is None, context
                    if shape == "misplaced-as-published":
                        assert any(
                            "as_published is only valid on the Located value"
                            in detail
                            for _formula, _raw_value, detail in errors
                        ), context
                    continue

                located = parsed
                if field == "species_loss_fraction":
                    assert isinstance(parsed, Mapping), context
                    located = parsed.get("Na")
                assert located is not None, context
                if unit_key.casefold() == "output_unit":
                    inference = located.inference
                    assert inference is not None, context
                    assert inference.output_unit == "g", context
                elif (
                    field in {"mass_kg", "final_mass_kg"}
                    and located.inference is not None
                ):
                    assert located.state.is_value, context
                    assert located.inference.relation == "g_to_kg", context
                    assert located.inference.output_unit == "kg", context
                elif (
                    field in {"mass_kg", "final_mass_kg"}
                    and unit_key in {"unit", "units"}
                ):
                    assert located.state.is_value, context
                    printed = Decimal(
                        "0.0008" if field == "final_mass_kg" else "0.25"
                    )
                    assert located.state.value == Value.point_of(
                        printed / Decimal(1000)
                    ), context
                else:
                    pytest.fail(
                        "unit-like key was accepted without a consuming reader: "
                        f"{context}"
                    )
