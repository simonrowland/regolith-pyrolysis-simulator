"""Render pins and migration checks for Shornikov et al. (2011)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2011-cao-mgo-alumina-melts.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [row for block in document["species"].values() for row in block["observations"]]


def _observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _render_pins(document: dict) -> None:
    observations = _observations(document)
    table1 = [row for row in observations if "_p001_t01_" in row["observation_id"]]
    assert len(table1) == 32, "Table 1 has 11 solid, 7 liquid, and 14 gas rows"
    assert _observation(
        document,
        "Al2O3",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r03_c02",
    )["values"]["value"] == "-1046.788"
    assert _observation(
        document,
        "CaO",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r04_c02",
    )["values"]["value"] == "-425.847"
    assert _observation(
        document,
        "O3",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r16_c06",
    )["values"]["value"] == "271.522"

    marker_pins = (
        ("CaO", "a", 0.50, "(1402, 3436)", "(1402, 3434)", "0.3038"),
        ("MgO", "b", 0.40, "(1374, 4738)", "(1374, 4736)", "0.3996"),
        ("Al2O3", "c", 0.010, "(2691, 4043)", "(2689, 4043)", "1.9954"),
    )
    for species, panel, activity, first_pixel, second_pixel, log_ordinate in marker_pins:
        row = _observation(
            document,
            species,
            f"shornikov_2011_fig1_{panel}_allibert_marker_01",
        )
        assert row["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "1960"
        assert row["values"]["activity"] == activity
        assert row["values"]["method_class"] == "figure_only"
        assert first_pixel in row["locator"]["note"]
        assert second_pixel in row["locator"]["note"]
        assert log_ordinate in row["locator"]["note"]
        assert row.get("uncertainty", {}).get("read_uncertainty_dex") != 0.0

    minimum = _observation(
        document,
        "CaO-MgO-Al2O3_melt",
        "shornikov_2011_text_minimum_delta_fG1960_melt",
    )
    assert minimum["values"]["value_as_printed"] == "−31.5"
    assert minimum["values"]["value"] == "-31.5"
    assert minimum["values"]["method_class"] == "quoted_attributed"
    assert minimum["values"]["composition_Al2O3_mole_pct_as_printed"] == "45"
    assert minimum["values"]["T_K"] == "1960"
    assert minimum["phase"] == "l"
    assert "from oxides" in minimum["standard_state"]
    assert (
        minimum["point_conditions"]["composition"]["state"]["reason"]
        == "incomplete_melt_composition"
    )
    assert document["species"]["Ca3MgAl4O10"]["context"][0]["values"]["concentration_mole_pct"] == {
        "Al2O3": "33.3",
    }


def test_tables_and_figure_markers_match_the_render_pins() -> None:
    document = _document()
    observations = _observations(document)
    assert len(observations) == 60
    assert len(document["species"]["MgO"]["context"]) == 12
    table2 = document["species"]["MgO"]["context"]
    assert table2[0]["values"]["concentration_wt_pct"] == {
        "CaO": "52.84",
        "MgO": "7.16",
        "Al2O3": "40.00",
    }
    assert table2[-1]["values"]["concentration_mole_pct"] == {
        "CaO": "31.53",
        "MgO": "18.46",
        "Al2O3": "50.00",
    }
    markers = [row for row in observations if "_allibert_marker_" in row["observation_id"]]
    unavailable = [row for row in observations if row["values"].get("unavailable_reason")]
    assert len(markers) == 19
    assert len(observations) == 60
    assert len(unavailable) == 8
    for row in markers:
        uncertainty = row.get("uncertainty", {})
        assert uncertainty.get("read_uncertainty_dex") != 0.0
        assert uncertainty.get("read_uncertainty_kJ_mol") != 0.0
    assert len(document["species"]["Al2O3"]["context"]) == 1
    assert check_all_fidelity_samples_match(document) == []
    _render_pins(document)


def test_render_pins_fail_when_a_digitized_value_is_mutated() -> None:
    document = deepcopy(_document())
    row = _observation(
        document,
        "MgO",
        "shornikov_2011_fig1_b_allibert_marker_01",
    )
    row["values"]["activity"] = 0.41
    with pytest.raises(AssertionError):
        _render_pins(document)


def test_render_pins_fail_when_migrated_evidence_class_is_mutated() -> None:
    document = deepcopy(_document())
    row = _observation(
        document,
        "Al2O3",
        "shornikov_2011_fig1_c_allibert_marker_01",
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _render_pins(document)


def test_isolated_migration_preserves_temperature_conditions_and_unavailable_curves() -> None:
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())

    assert len(observations) == 60
    assert sum(row.admission.status.value == "admitted" for row in observations) == 52
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 8
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    minimum = next(
        row
        for row in observations
        if row.observation_id.endswith("::shornikov_2011_text_minimum_delta_fG1960_melt")
    )
    assert minimum.admission.status.value == "admitted"
    assert minimum.value.kind is ValueKind.POINT
    assert minimum.identity.temperature_K is not None
    assert minimum.point_conditions["composition"].state.is_unknown
    assert minimum.point_conditions["composition"].locator is not None
    for row in observations:
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        condition = row.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None

    for row in observations:
        if "fig1_" in row.observation_id:
            assert {"composition", "total_pressure_Pa", "fO2_Pa"} <= set(
                row.point_conditions
            )
            assert row.point_conditions["composition"].locator is not None
