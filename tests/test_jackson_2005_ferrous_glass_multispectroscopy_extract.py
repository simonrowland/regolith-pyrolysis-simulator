"""Render-grid and migration checks for Jackson et al. (2005)."""

from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "jackson-2005-ferrous-glass-multispectroscopy"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
RENDER_GRID = ROOT / f"tests/fixtures/literature_extracts/{SID}-render-grid.csv"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _typed_rows(document: dict, species: str, quantity: str) -> dict[str, tuple[dict, dict]]:
    rows = {}
    for observation in document["species"][species]["observations"]:
        values = observation["values"]
        if values.get("quantity") != quantity:
            continue
        points = values.get("points", ())
        if not points:
            sample = observation.get("point_conditions", {}).get("sample_id", {}).get("state", {}).get("value")
            if sample is not None:
                rows[str(sample)] = (observation, {})
        for point in points:
            sample = str(point.get("sample_as_published") or point.get("row_as_printed"))
            rows[sample] = (observation, point)
    return rows


def _assert_render_grid_matches(document: dict, grid: list[dict[str, str]]) -> None:
    assert [row["sample"] for row in grid] == ["LI2", "RB2", "NA1", "NC6", "NC2", "NC1", "BAS"]
    mossbauer = {}
    for observation in document["species"]["Fe"]["observations"]:
        if (
            observation["values"].get("quantity") != "valence_ratio"
            or observation["locator"].get("table") != "1"
        ):
            continue
        for point in observation["values"].get("points", ()):
            mossbauer[point["sample_as_published"]] = (observation, point)
    chromium = _typed_rows(document, "Cr2O3", "equilibrium_phase_composition")
    assert set(mossbauer) == {"LI2", "RB2", "NC6"}
    assert set(chromium) == {"NC1", "NC2"}

    for rendered in grid:
        sample = rendered["sample"]
        expected_fe = rendered["mossbauer_feiii_percent"]
        if expected_fe:
            observation, point = mossbauer[sample]
            assert point["value"] == expected_fe
            assert observation["locator"]["page"] == 4
            assert observation["locator"]["row"] == sample
        else:
            assert sample not in mossbauer

        expected_cr = rendered["cr2o3_found_wt_percent"]
        if expected_cr:
            observation, _ = chromium[sample]
            assert observation["values"]["value_as_published"] == f"{expected_cr} wt.%"
            assert observation["values"]["value"] == expected_cr.removeprefix("~")
            assert observation["values"]["approximate"] is True
            assert observation["locator"]["page"] == 2
        else:
            assert sample not in chromium


def test_render_transcription_pins_typed_rows_and_mutations_fail() -> None:
    document = _document()
    grid = _grid()
    assert check_all_fidelity_samples_match(document, label=SID) == []
    _assert_render_grid_matches(document, grid)

    changed_grid = deepcopy(grid)
    changed_grid[0]["mossbauer_feiii_percent"] = "8"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(document, changed_grid)

    changed_document = deepcopy(document)
    li2 = _typed_rows(changed_document, "Fe", "valence_ratio")["LI2"][1]
    li2["value"] = "8"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(changed_document, grid)


def test_isolated_migration_preserves_species_composition_conditions_and_quench() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 6

    source_by_id = {
        observation["observation_id"]: (formula, observation)
        for formula, body in document["species"].items()
        for observation in body.get("observations", ())
    }
    assert set(source_by_id) == {
        "jackson_2005_mossbauer_li2_fe3_fraction",
        "jackson_2005_mossbauer_rb2_fe3_fraction",
        "jackson_2005_mossbauer_nc6_fe3_fraction",
        "jackson_2005_xanes_rb2_fe3_fraction",
        "jackson_2005_nc1_cr2o3_found_composition",
        "jackson_2005_nc2_cr2o3_found_composition",
    }

    migrated_by_id = {
        source_id: observation
        for source_id in source_by_id
        for observation in result.observations.values()
        if f"::{source_id}" in observation.observation_id
    }
    for source_id, (formula, source) in source_by_id.items():
        observation = migrated_by_id[source_id]
        assert observation.identity.species.formula == formula
        assert observation.locator.pdf_page_index == source["locator"]["page"] - 1
        assert observation.admission.status.value == "admitted"

        conditions = observation.point_conditions or {}
        assert "sample_id" in conditions
        sample = source["values"].get("points", [None])[0]
        expected_sample = (
            sample["sample_as_published"] if sample else source["point_conditions"]["sample_id"]["state"]["value"]
        )
        assert conditions["sample_id"].state.value == expected_sample

        if formula == "Fe":
            assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
            assert observation.evidence.class_.value == "measured_reduced"
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal(source["values"]["points"][0]["T_K"])
            assert observation.identity.total_pressure_Pa is not None
            assert observation.identity.total_pressure_Pa.is_value
            assert observation.identity.total_pressure_Pa.value == Decimal("100000")
            assert observation.identity.fO2_Pa is not None and observation.identity.fO2_Pa.is_unknown
            assert observation.identity.composition is not None
            assert observation.identity.composition.is_value
            assert observation.identity.composition.value.amount_basis is AmountBasis.MOL_INVENTORY
            source_composition = source["values"]["points"][0]["point_conditions"]["composition"]["state"]["value"]["components"]
            assert dict(observation.identity.composition.value.components) == {
                element: Decimal(amount) for element, amount in source_composition
            }
            assert conditions["temperature_K"].state.is_value
            assert conditions["composition"].state.is_value
            assert conditions["fO2_Pa"].state.is_unknown
            assert conditions["total_pressure_Pa"].state.value == Decimal("100000")
            assert source["values"]["points"][0]["quench_statement"]["preservation"] == "not_stated"
            assert source["values"]["points"][0]["quench_statement"]["quench_direction"] == "not_stated"
        else:
            assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            assert observation.evidence.class_.value == "measured_direct"
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_unknown
            assert observation.identity.composition is not None
            assert observation.identity.composition.is_not_applicable
            assert observation.value.approximate is True

    expected_fe = {
        "jackson_2005_mossbauer_li2_fe3_fraction": Decimal("0.07"),
        "jackson_2005_mossbauer_rb2_fe3_fraction": Decimal("0.22"),
        "jackson_2005_mossbauer_nc6_fe3_fraction": Decimal("0.10"),
        "jackson_2005_xanes_rb2_fe3_fraction": Decimal("0.15"),
    }
    for source_id, expected in expected_fe.items():
        assert migrated_by_id[source_id].value.point == expected
