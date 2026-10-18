"""Rendered table pins and isolated migration for Shornikov et al. (2020)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2020-mgo-feo-melts.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [row for block in document["species"].values() for row in block.get("observations", [])]


def _observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _observation_by_id(document: dict, observation_id: str) -> dict:
    return next(row for row in _observations(document) if row["observation_id"] == observation_id)


def _table_pins(document: dict) -> None:
    observations = _observations(document)
    table2 = [row for row in observations if "_p002_t03_" in row["observation_id"]]
    table1 = document["species"]["FeO"]["context"]
    assert len(table2) == 17
    assert len(table1) == 14
    assert _observation(
        document,
        "MgO",
        "shornikov_2020_mgo_feo_melts_p002_t03_r03_c02",
    )["values"]["value"] == "-355.557"
    assert _observation(
        document,
        "FeO",
        "shornikov_2020_mgo_feo_melts_p002_t03_r06_c04",
    )["values"]["value"] == "-134.332"
    assert _observation(
        document,
        "O4",
        "shornikov_2020_mgo_feo_melts_p002_t03_r13_c06",
    )["values"]["value"] == "240.548"
    for row in table2:
        assert row["values"]["T_K"] == "2500"
        assert row["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "2500"
        assert row["point_conditions"]["temperature_K"]["locator"]["page"] == row["locator"]["page"]
    assert table1[0]["values"]["T_as_printed"] == "1133–1333"
    assert table1[4]["values"]["reference_as_printed"] == "Gordeev et al., 1965"
    assert check_all_fidelity_samples_match(document) == []


def _figure_pins(document: dict) -> None:
    pins = (
        (
            "shornikov_2020_fig2a_s1_marker_01",
            "1173",
            "2",
            "5",
            [1061.5, 2424.5],
            [1061.9, 2424.0],
        ),
        (
            "shornikov_2020_fig2a_s6_marker_01",
            "1173",
            "2.8",
            "3.8",
            [1042.5, 1963.5],
            [1042.3, 1964.0],
        ),
        (
            "shornikov_2020_fig2b_s4_marker_01",
            "1473",
            "2.3",
            "10",
            [2975.5, 2248.5],
            [2975.5, 2252.3],
        ),
    )
    for observation_id, temperature, gamma, feo_pct, read_1, read_2 in pins:
        row = _observation_by_id(document, observation_id)
        assert row["values"]["T_K"] == temperature
        assert row["values"]["gamma"] == gamma
        assert row["values"]["FeO_mol_pct_as_plotted"] == feo_pct
        assert row["locator"]["pixel_read_1_page"] == read_1
        assert row["locator"]["pixel_read_2_page"] == read_2
    for row in _observations(document):
        if str(row.get("locator", {}).get("figure")) not in {"1", "2"}:
            continue
        if row.get("values", {}).get("admission_status") != "admitted":
            continue
        for key, value in row.get("uncertainty", {}).items():
            if key.startswith("read_uncertainty_"):
                assert float(value) > 0


def test_tables_match_600_dpi_render_pins_and_row_counts() -> None:
    _table_pins(_document())


def test_figure_reads_match_literal_render_pins() -> None:
    _figure_pins(_document())


def test_render_pins_fail_when_a_table_value_is_mutated() -> None:
    document = deepcopy(_document())
    _observation(
        document,
        "O4",
        "shornikov_2020_mgo_feo_melts_p002_t03_r13_c06",
    )["values"]["value"] = "240.549"
    with pytest.raises(AssertionError):
        _table_pins(document)


def test_figure_pins_fail_when_a_digitized_value_is_mutated() -> None:
    document = deepcopy(_document())
    _observation_by_id(
        document,
        "shornikov_2020_fig2a_s1_marker_01",
    )["values"]["gamma"] = "2.1"
    with pytest.raises(AssertionError):
        _figure_pins(document)


def test_isolated_migration_preserves_conditions_for_all_observations() -> None:
    document = _document()
    figure_context = [
        row
        for row in document["species"]["MgO-FeO_melt"]["context"]
        if row["observation_id"].startswith("shornikov_2020_fig3_panel_")
        or row["observation_id"].startswith("shornikov_2020_fig4b_series")
    ]
    assert len(figure_context) == 17
    assert all(row["type"] == "thermodynamic_context" for row in figure_context)
    assert all(
        set(row["values"]) == {"quantity", "reason"}
        and row["values"]["quantity"] == "unscored_figure_context"
        and row["values"].get("reason")
        and row.get("attribution")
        for row in figure_context
    )
    assert all("point_conditions" not in row for row in figure_context)
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 147
    assert sum(row.admission.status.value == "admitted" for row in observations) == 127
    assert sum(row.admission.status.value == "rejected" for row in observations) == 20
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 20
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    source_rows = {row["observation_id"]: row for row in _observations(document)}
    for row in observations:
        assert row.point_conditions is not None
        assert "temperature_K" in row.point_conditions
        temperature = row.point_conditions["temperature_K"]
        assert temperature.locator is not None
        source_id = row.observation_id.split("::", 1)[-1]
        source_temperature = source_rows[source_id].get("values", {}).get("T_K")
        if source_temperature is not None:
            assert temperature.state.is_value
            assert float(temperature.state.value) == float(source_temperature)
        else:
            assert temperature.state.is_value or temperature.state.value is None
        for condition in row.point_conditions.values():
            assert condition.locator is not None
