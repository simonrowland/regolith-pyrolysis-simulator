from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, UncertaintyKind, ValueKind
from simulator.battery.identity import celsius_to_kelvin
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
    read_observation_grid_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/babaian-2026-fe-mg-si-reassessment.yaml"
RENDER_GRID = ROOT / "tests/fixtures/babaian-2026-render-grid.tsv"
GRID_FIELDS = (
    "observation_id",
    "species_formula",
    "sample_id",
    "experiment",
    "T_C",
    "phase_label",
    "phase_label_as_published",
    "condition_as_published",
    "pO2_Pa",
    "preheat_as_published",
    "basis",
    "phase_role",
    "component_amount",
    "component_amount_as_published",
    "sigma",
    "page",
    "table",
)


def _read_fixture() -> list[dict[str, str]]:
    return read_observation_grid_fixture(
        RENDER_GRID, ("T_C", "pO2_Pa", "component_amount", "sigma")
    )


def _extract_grid(document: dict) -> list[dict[str, str]]:
    rows = []
    for species_formula, species in document["species"].items():
        for observation in species.get("observations", []):
            values = observation["values"]
            oxygen = values.get("point_conditions", {}).get("pO2_Pa", {})
            oxygen_state = oxygen.get("state", {})
            uncertainty = observation.get("uncertainty") or {}
            row = {
                "observation_id": observation["observation_id"],
                "species_formula": values.get("formula", species_formula),
                "sample_id": values["sample_id_as_published"],
                "experiment": observation["experiment"],
                "T_C": decimal_text(values["T_C"]),
                "phase_label": values["phase_label"],
                "phase_label_as_published": values["phase_label_as_published"],
                "condition_as_published": values.get(
                    "pO2_as_published", values.get("redox_condition_as_published", "")
                ),
                "pO2_Pa": decimal_text(oxygen_state.get("value")),
                "preheat_as_published": str(values["preheat_as_published"]),
                "basis": values["basis"],
                "phase_role": values["phase_label"],
                "component_amount": decimal_text(values["component_amount"]),
                "component_amount_as_published": str(values["component_amount_as_published"]),
                "sigma": decimal_text(uncertainty.get("sigma")),
                "page": str(observation["locator"]["page"]),
                "table": str(observation["locator"]["table"]),
            }
            rows.append(row)
    return sorted(rows, key=lambda row: row["observation_id"])


def _assert_grid_matches(document: dict, fixture: list[dict[str, str]]) -> None:
    assert_observation_grid_matches(_extract_grid(document), fixture, GRID_FIELDS)


def test_render_grid_covers_every_typed_component_and_rejects_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    fixture = _read_fixture()
    _assert_grid_matches(document, fixture)
    assert len(fixture) == 224
    expected_figure_pages = {
        1: 9, 2: 10, 3: 10, 4: 11, 5: 22, 6: 22, 7: 22,
        8: 23, 9: 23, 10: 24, 11: 25, 12: 25, 13: 25,
        14: 26, 15: 26, 16: 27, 17: 27, 18: 28, 19: 28,
        20: 29, 21: 29, 22: 30, 23: 31, 24: 32,
    }
    figure_records = [
        record
        for species in document["species"].values()
        for record in species.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert {
        record["values"]["figure"]: record["locator"]["page"]
        for record in figure_records
    } == expected_figure_pages
    assert all(record["values"]["points"] == [] for record in figure_records)
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        for record in figure_records
    )

    mutated_grid = deepcopy(fixture)
    mutated_grid[0]["component_amount"] = str(
        Decimal(mutated_grid[0]["component_amount"]) + Decimal("1")
    )
    with pytest.raises(AssertionError):
        _assert_grid_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    first = _extract_grid(mutated_extract)[0]["observation_id"]
    observation = next(
        observation
        for species in mutated_extract["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == first
    )
    observation["values"]["component_amount"] += 1
    with pytest.raises(AssertionError):
        _assert_grid_matches(mutated_extract, fixture)


def test_every_typed_component_migrates_with_its_formula_conditions_and_point() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    fixture = _read_fixture()
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == len(fixture) == 224
    assert not migrator.result.validation.hard_issues
    expected_experiments: dict[str, str] = {}

    for row in fixture:
        observation = observations[row["observation_id"]]
        expected_temperature = celsius_to_kelvin(Decimal(row["T_C"]))
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(row["component_amount"])
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == row["species_formula"]
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole(row["phase_role"])
        assert observation.identity.subtype.value == row["basis"]
        assert observation.identity.temperature_K.value == expected_temperature
        assert observation.point_conditions["T_C"].state.value == Decimal(row["T_C"])
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == expected_temperature
        assert temperature.locator is not None
        assert temperature.locator.page == int(row["page"])
        assert temperature.locator.pdf_page_index == int(row["page"]) - 1
        if row["pO2_Pa"]:
            expected_oxygen = Decimal(row["pO2_Pa"])
            assert observation.identity.fO2_Pa.value == expected_oxygen
            oxygen = observation.point_conditions["pO2_Pa"]
            assert oxygen.state.value == expected_oxygen
            assert oxygen.locator is not None
            assert oxygen.locator.page == int(row["page"])
        else:
            assert observation.identity.fO2_Pa.is_unknown
            assert "pO2_Pa" not in observation.point_conditions

        if row["sigma"]:
            assert observation.uncertainty.kind is UncertaintyKind.PRINTED
            assert observation.uncertainty.verbatim["reported_status"] == "printed"
            assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(
                row["sigma"]
            )
            assert observation.uncertainty.verbatim["units"] == "mol.%"
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE
        assert not observation.notices

        experiment_id = expected_experiments.setdefault(row["experiment"], observation.experiment_id)
        assert observation.experiment_id == experiment_id
        assert observation.experiment_id in migrator.result.experiments

    assert len(expected_experiments) == 63
    _assert_grid_matches(document, fixture)
