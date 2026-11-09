from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import RefusalReason, VaporizationMode
from simulator.battery.migrate import load_migrated_store, migrate
from simulator.battery.records import Locator, Value
from tests.battery.test_run_metadata_schema import (
    _run_metadata_shape_extract,
    _run_metadata_value_shape,
    _unitless_run_metadata_extract,
)
from tools.validate_literature_extracts import validate_extract_document


@pytest.mark.parametrize(
    "witness",
    ("inner-located-value", "species-loss-fraction", "vaporization-statement"),
)
def test_consumed_inference_witnesses_round_trip(
    tmp_path: Path,
    witness: str,
) -> None:
    if witness == "inner-located-value":
        field = "mass_kg"
        raw_value = _run_metadata_value_shape("inner-inference", "0.0005")
        root, extract = _run_metadata_shape_extract(
            tmp_path / witness,
            field,
            "experiments[].sample.mass_kg",
            "inner-inference",
            raw_value=raw_value,
        )
        expected_unit = "kg"
        expected_table = "inner-inference"
        expected_page = 13
    elif witness == "species-loss-fraction":
        field = "species_loss_fraction"
        raw_value = {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.25"},
            },
            "locator": {"table": "grok-species-loss", "page": 6},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "fraction",
            },
        }
        root, extract = _run_metadata_shape_extract(
            tmp_path / witness,
            field,
            "experiments[].sample.species_loss_fraction",
            "scalar",
            raw_value=raw_value,
        )
        expected_unit = "fraction"
        expected_table = "grok-species-loss"
        expected_page = 6
    else:
        field = "vaporization_statement"
        payload = {
            "state": {"tag": "value", "value": "congruent"},
            "locator": {"table": "grok-vaporization", "page": 8},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "as_published",
            },
        }
        root, extract = _unitless_run_metadata_extract(
            tmp_path / witness,
            field,
            "experiments[].vaporization_statement",
            payload,
        )
        expected_unit = "as_published"
        expected_table = "grok-vaporization"
        expected_page = 8

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    if field == "species_loss_fraction":
        assert experiment.sample.species_loss_fraction is not None
        located = experiment.sample.species_loss_fraction["Na"]
        assert located.state.value == Value.point_of("0.25")
    elif field == "vaporization_statement":
        located = experiment.vaporization_statement
        assert located is not None
        assert located.state.value is VaporizationMode.CONGRUENT
    else:
        located = experiment.sample.mass_kg
        assert located is not None
        assert located.state.value == Value.point_of("0.0005")

    assert located.locator == Locator(table=expected_table, page=expected_page)
    assert located.inference is not None
    assert located.inference.relation == "identity"
    assert located.inference.inputs == ("source",)
    assert located.inference.output_unit == expected_unit


def test_mapping_inference_input_round_trips_without_wrapper_repr(
    tmp_path: Path,
) -> None:
    raw_value = {
        "value": {
            "state": {"tag": "value", "value": "0.0005"},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": [{"source": "printed-table", "row": 3}],
                "output_unit": "kg",
            },
        },
        "locator": {"table": "mapping-input", "page": 3},
    }
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "species.*.observations[].equipment.mass_kg",
        "inner-inference",
        raw_value=raw_value,
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.inference is not None
    assert experiment.sample.mass_kg.inference.inputs == (
        "{'source': 'printed-table', 'row': 3}",
    )
    assert "object at 0x" not in repr(experiments)


def test_locator_inference_extra_round_trips_without_wrapper_repr(
    tmp_path: Path,
) -> None:
    raw_value = {
        "state": {"tag": "value", "value": "0.0005"},
    }
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "species.*.observations[].equipment.mass_kg",
        "scalar",
        raw_value=raw_value,
    )
    equipment = extract["species"]["Na"]["observations"][0]["equipment"]
    equipment["mass_kg"]["locator"]["inference"] = {
        "method": "fixture",
        "relation": "identity",
        "inputs": ["source"],
    }
    extract_path = root / "data" / "literature" / "extracts" / "fixture-source.yaml"
    extract_path.write_text(
        yaml.safe_dump(extract, sort_keys=False), encoding="utf-8"
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.locator is not None
    assert experiment.sample.mass_kg.locator.note is not None
    assert (
        "inference={'method': 'fixture'" in experiment.sample.mass_kg.locator.note
    )
    assert "object at 0x" not in repr(experiments)


def test_non_mapping_inference_is_not_silently_dropped(tmp_path: Path) -> None:
    raw_value = {
        "state": {"tag": "value", "value": "0.0005"},
        "locator": {"table": "ignored-inference", "page": 5},
        "inference": "provenance-to-keep",
    }
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "scalar",
        raw_value=raw_value,
    )

    assert any(
        "experiments[0].sample.mass_kg" in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues
