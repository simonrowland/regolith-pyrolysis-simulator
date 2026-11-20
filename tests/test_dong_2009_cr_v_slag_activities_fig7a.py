from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts-v2/dong-2009-cr-v-slag-activities.yaml"
DIGITIZATION = ROOT / "_corpus-out/tables/dong-2009-cr-v-slag-activities/f7a.csv"
PINS = {
    "1803-3": ("0.00442", "0.0195"),
    "1873-2": ("0.00065", "0.0023"),
    "1923-3": ("0.00134", "0.0082"),
}


def _rows(document: dict) -> dict[str, dict]:
    return {
        point["row_as_digitized"]: point
        for observation in document["species"]["CrO"]["observations"]
        for point in observation["values"]["points"]
    }


def _assert_digitization_pins(document: dict, digitization: dict[str, dict]) -> None:
    points = _rows(document)
    for row_id, (x_cr_o, activity) in PINS.items():
        assert points[row_id]["x_CrO"] == digitization[row_id]["x_CrO"] == x_cr_o
        assert points[row_id]["activity"] == digitization[row_id]["activity_CrO"] == activity


def test_figure_digitization_pins_match_csv_and_detect_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with DIGITIZATION.open(encoding="utf-8", newline="") as source:
        digitization = {row["row_id"]: row for row in csv.DictReader(source)}
    _assert_digitization_pins(document, digitization)
    for row_id, point in _rows(document).items():
        csv_row = digitization[row_id]
        uncertainty = point["digitization_uncertainty"]
        assert uncertainty["x_CrO"].removeprefix("±") == csv_row["x_uncertainty"]
        assert uncertainty["activity"].removeprefix("±") == csv_row["activity_uncertainty"]
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    _rows(mutated)["1873-2"]["activity"] = "0.0024"
    with pytest.raises(AssertionError):
        _assert_digitization_pins(mutated, digitization)
    assert check_all_fidelity_samples_match(mutated, label="mutated Fig. 7(a)")


def test_isolated_migration_preserves_all_nine_located_rows() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 9

    observations = [
        row
        for row in result.observations.values()
        if row.value.kind is ValueKind.POINT
    ]
    assert len(observations) == 9
    source_points = _rows(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    assert len(source_points) == 9
    for point in source_points.values():
        conditions = point["point_conditions"]
        printed_temp_k = conditions["temperature_K"]["state"]["value"]
        temp_k = Decimal(point["T_C"]) + Decimal("273.15")
        migrated = next(
            row
            for row in observations
            if row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == temp_k
            and row.value.point == Decimal(point["activity"])
        )
        assert migrated.identity.temperature_K.value == temp_k
        assert migrated.identity.reference_state.is_unknown
        assert migrated.identity.reference_state.reason == "not printed"
        assert migrated.point_conditions is not None
        condition_keys = (
            "temperature_K",
            "pO2_Pa",
            "x_CrO",
            "composition",
            "base_slag_system",
            "Cr2O3_reagent",
            "nominal_CrOx_loading",
            "targeted_mixture_mass",
            "gas_mix",
            "gas_mix_ratio",
            "chromium_valence_assumption",
        )
        for key in condition_keys:
            condition = conditions[key]
            assert condition["locator"]
            assert key in migrated.point_conditions
            located = migrated.point_conditions[key]
            assert located.locator is not None
            source_state = condition["state"]
            if source_state["tag"] == "value":
                assert located.state.is_value
                expected = source_state["value"]
                if key in {"temperature_K", "pO2_Pa", "x_CrO"}:
                    assert located.state.value == Decimal(expected)
                else:
                    assert located.state.value == expected
            else:
                assert located.state.is_unknown
                assert located.state.reason == source_state["reason"]
        assert migrated.point_conditions["temperature_K"].state.value == Decimal(
            printed_temp_k
        )
