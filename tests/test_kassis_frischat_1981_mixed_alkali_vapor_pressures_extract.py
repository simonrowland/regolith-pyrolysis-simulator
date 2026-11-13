from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / (
    "data/literature/extracts/"
    "kassis-frischat-1981-mixed-alkali-vapor-pressures.yaml"
)
OBSERVATION_IDS = {
    "kassis_frischat_1981_table3_rb_pressure_ratio_1150c": ("0,44", 1150, 2),
    "kassis_frischat_1981_table3_rb_pressure_ratio_1200c": ("0,46", 1200, 3),
    "kassis_frischat_1981_table3_rb_pressure_ratio_1250c": ("0,48", 1250, 4),
    "kassis_frischat_1981_table3_rb_pressure_ratio_1300c": ("0,56", 1300, 5),
}


def _document(path: Path = EXTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _table3_rows(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for observation in document["species"]["Rb"]["observations"]
        if observation.get("locator", {}).get("table") == "3"
    }


def _assert_rendered_values_and_count(document: dict) -> None:
    assert set(document["species"]) == {"Rb"}
    assert sum(
        len(block.get("observations") or [])
        for block in document["species"].values()
    ) == 4
    rows = _table3_rows(document)
    assert set(rows) == set(OBSERVATION_IDS)
    assert len(rows) == 4
    for observation_id, (printed_value, temperature, column) in OBSERVATION_IDS.items():
        observation = rows[observation_id]
        locator = observation["locator"]
        assert locator["page"] == 7
        assert locator["published_page"] == 95
        assert locator["table"] == "3"
        assert locator["table_row_number"] == 2
        assert locator["table_column_number"] == column
        assert observation["values"]["value"] == printed_value
        assert observation["values"]["T_C"] == str(temperature)
        assert observation["values"].get("schema_gap")
        assert observation["values"]["method_class"] == "measured_reduced"
        assert "attribution" not in observation["values"]
        derivation = observation["derivation"]
        assert "Eqs. (19)–(20) and (23)–(26)" in derivation["relation"]
        assert derivation["output_unit"] == "dimensionless"
        assert derivation["raw_inputs"]["state"] == {
            "tag": "value",
            "value": "not_printed",
        }
        assert derivation["raw_inputs"]["locator"]["page"] == 8
        assert derivation["raw_inputs"]["locator"]["published_page"] == 96
        assert derivation["raw_inputs"]["locator"]["figure"] == "8a, 8b, 9a, 9b"
        assert observation["experiment"] == "mixed_alkali_pressure_ratio_series"

    numerator = rows[next(iter(OBSERVATION_IDS))]["values"][
        "compared_melts_as_published"
    ]["numerator"]
    denominator = rows[next(iter(OBSERVATION_IDS))]["values"][
        "compared_melts_as_published"
    ]["denominator"]
    assert numerator["locator"]["page"] == 2
    assert numerator["locator"]["published_page"] == 90
    assert numerator["locator"]["table"] == "1"
    assert numerator["locator"]["row_label"] == "Schmelze Nr. 11"
    assert numerator["melt_number"] == "11"
    assert numerator["gamma_Rb_as_printed"] == "0,3"
    assert numerator["composition_mol_percent"] == {
        "Na2O": "17,5",
        "Rb2O": "7,5",
        "SiO2": "75",
    }
    assert denominator["locator"]["page"] == 2
    assert denominator["locator"]["published_page"] == 90
    assert denominator["locator"]["table"] == "1"
    assert denominator["locator"]["row_label"] == "Schmelze Nr. 3"
    assert denominator["melt_number"] == "3"
    assert denominator["gamma_Rb_as_printed"] == "0,5"
    assert denominator["composition_mol_percent"] == {
        "Na2O": "12,5",
        "Rb2O": "12,5",
        "SiO2": "75",
    }


def test_render_pins_cover_table3_ratio_and_table1_compositions() -> None:
    document = _document()
    _assert_rendered_values_and_count(document)

    mutated = deepcopy(document)
    mutated_rows = _table3_rows(mutated)
    mutated_rows[
        "kassis_frischat_1981_table3_rb_pressure_ratio_1150c"
    ]["values"]["value"] = "0,45"
    with pytest.raises(AssertionError):
        _assert_rendered_values_and_count(mutated)

    mutated_class = deepcopy(document)
    _table3_rows(mutated_class)[
        "kassis_frischat_1981_table3_rb_pressure_ratio_1150c"
    ]["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_rendered_values_and_count(mutated_class)


def test_isolated_migration_keeps_conditions_and_exact_schema_refusal() -> None:
    document = _document()
    _assert_rendered_values_and_count(document)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()

    expected_ids = set(OBSERVATION_IDS)
    migrated = {
        observation_id: observation
        for source_id in expected_ids
        for observation in result.observations.values()
        if observation.observation_id.endswith(f"::{source_id}")
        for observation_id in [source_id]
    }
    assert set(migrated) == expected_ids
    assert len(migrated) == 4
    assert sum(obs.value.kind is ValueKind.UNAVAILABLE for obs in migrated.values()) == 4
    assert {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.identity.quantity.is_unknown
    } == expected_ids
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in migrated.values()
    )

    experiment = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("::mixed_alkali_pressure_ratio_series")
    )
    assert experiment.pressure_environment.total_pressure_Pa.state.is_value
    assert float(
        experiment.pressure_environment.total_pressure_Pa.state.value.point
    ) == pytest.approx(6.6e-4)

    for observation_id, observation in migrated.items():
        _, temperature, _ = OBSERVATION_IDS[observation_id]
        assert observation.identity.temperature_K.is_value
        assert float(observation.identity.temperature_K.value) == pytest.approx(
            temperature + 273.15
        )
        # The measured condition is the chamber background; the in-cell vapor
        # total pressure is calculated, not printed, and is not an identity axis.
        assert observation.identity.total_pressure_Pa is None
        assert observation.identity.fO2_Pa is None
        assert observation.identity.composition.is_unknown
        assert observation.point_conditions["temperature_C"].state.is_value
        assert float(
            observation.point_conditions["temperature_C"].state.value
        ) == temperature
        assert "total_pressure_Pa" not in observation.point_conditions
        fO2_condition = observation.point_conditions.get("fO2_Pa")
        assert fO2_condition is None or fO2_condition.state.is_unknown

    assert len(migrated) == 4  # four printed pressure-ratio cells; no figure points
