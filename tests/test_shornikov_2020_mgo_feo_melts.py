"""Rendered table pins and isolated migration for Shornikov et al. (2020)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
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


def _figure_marker_rows(document: dict) -> list[dict]:
    return [
        row
        for row in _observations(document)
        if str(row.get("locator", {}).get("figure")) in {"1", "2"}
        and row.get("values", {}).get("FeO_mol_pct_as_plotted") is not None
    ]


def _gamma_read(row: dict, page_y: float) -> float:
    locator = row["locator"]
    tick_y = 1403 if locator["panel"] == "a" else 1405
    crop_y = page_y - 1650
    return 1 + (tick_y - crop_y) * 1.5 / 900


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
        "MgO",
        "shornikov_2020_mgo_feo_melts_p002_t03_r03_c04",
    )["phase"] == "l"
    feo_liquid = _observation(
        document,
        "FeO",
        "shornikov_2020_mgo_feo_melts_p002_t03_r06_c04",
    )
    assert feo_liquid["values"]["value"] == "-134.332"
    assert feo_liquid["phase"] == "l"
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
        assert row["values"]["MgO_mol_pct_as_binary_remainder"] == str(
            Decimal(100) - Decimal(feo_pct)
        )
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["phase"] == "cr"
        assert row["locator"]["pixel_read_1_page"] == read_1
        assert row["locator"]["pixel_read_2_page"] == read_2
    fig1 = _observation_by_id(
        document,
        "shornikov_2020_fig1_s2_solidus_marker_01",
    )
    assert fig1["values"]["T_K"] == "2.7e+03"
    assert fig1["values"]["FeO_mol_pct_as_plotted"] == "13"
    assert fig1["values"]["MgO_mol_pct_as_binary_remainder"] == "87"
    assert fig1["phase"] == "(Mg,Fe)O solid solution + liquid"
    assert fig1["values"]["admission_status"] == "typed_refusal"
    _figure_bands_and_compositions(document)


def _figure_bands_and_compositions(document: dict) -> None:
    rows = _figure_marker_rows(document)
    assert len(rows) == 110
    fig2_rows = [
        row
        for row in _observations(document)
        if str(row.get("locator", {}).get("figure")) == "2"
    ]
    assert len(fig2_rows) == 79
    assert all(row["phase"] == "cr" for row in fig2_rows)
    fig4a_curves = [
        row
        for row in _observations(document)
        if str(row.get("locator", {}).get("figure")) == "4"
        and row.get("locator", {}).get("panel") == "a"
        and row.get("values", {}).get("series") is not None
    ]
    assert len(fig4a_curves) == 8
    assert all(row["phase"] == "l" for row in fig4a_curves)
    for row in rows:
        values = row["values"]
        locator = row["locator"]
        figure = str(locator["figure"])
        feo = Decimal(values["FeO_mol_pct_as_plotted"])
        mgo = Decimal(values["MgO_mol_pct_as_binary_remainder"])
        assert feo + mgo == Decimal(100)
        assert values["admission_status"] == "typed_refusal"
        assert values["status"] == "typed_refusal"
        assert values["reason"]
        composition = row["point_conditions"]["composition"]["state"]
        assert composition["tag"] == "unknown"
        assert composition["reason"] == "mole_percent_basis_not_supported_by_schema"
        if figure == "1":
            stored = Decimal(values["T_K"])
            uncertainty = Decimal(str(row["uncertainty"]["read_uncertainty_K"]))
            half_bin = Decimal(1).scaleb(stored.adjusted() - 1) / 2
            assert uncertainty >= half_bin
            for pixel in (
                locator["pixel_read_1_page"],
                locator["pixel_read_2_page"],
            ):
                crop_y = Decimal(str(pixel[1])) - Decimal(2850)
                calibrated = Decimal(1600) + (
                    Decimal(1293) - crop_y
                ) * Decimal(1400) / Decimal(1027)
                assert abs(calibrated - stored) <= uncertainty
        else:
            assert row["phase"] == "cr"
            gamma = Decimal(values["gamma"])
            uncertainty = Decimal(str(row["uncertainty"]["read_uncertainty_gamma_FeO"]))
            assert uncertainty > 0
            half_bin = Decimal(1).scaleb(gamma.adjusted() - 1) / 2
            band_dex = float(uncertainty / (gamma * Decimal(10).ln()))
            for endpoint in (gamma - half_bin, gamma + half_bin):
                assert abs(math.log10(float(endpoint / gamma))) <= band_dex
            for pixel in (
                locator["pixel_read_1_page"],
                locator["pixel_read_2_page"],
            ):
                calibrated = _gamma_read(row, float(pixel[1]))
                assert abs(math.log10(calibrated / float(gamma))) <= band_dex


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


def test_figure_pins_fail_when_fig1_temperature_or_figure_phase_is_mutated() -> None:
    document = deepcopy(_document())
    _observation_by_id(
        document,
        "shornikov_2020_fig1_s2_solidus_marker_01",
    )["values"]["T_K"] = "2.6e+03"
    with pytest.raises(AssertionError):
        _figure_pins(document)

    document = deepcopy(_document())
    _observation_by_id(
        document,
        "shornikov_2020_fig2a_s1_marker_01",
    )["phase"] = "l"
    with pytest.raises(AssertionError):
        _figure_pins(document)

    document = deepcopy(_document())
    _observation_by_id(
        document,
        "shornikov_2020_fig1_s2_solidus_marker_01",
    )["phase"] = "cr"
    with pytest.raises(AssertionError):
        _figure_pins(document)


def test_isolated_migration_preserves_conditions_for_all_observations() -> None:
    document = _document()
    figure_context = [
        row
        for row in document["species"]["MgO-FeO_melt"]["context"]
        if row["observation_id"].startswith("shornikov_2020_fig3_panel_")
        or row["observation_id"].startswith("shornikov_2020_fig4b_series")
        or row["observation_id"] == "shornikov_2020_fig4a_unkeyed_black_markers"
    ]
    assert len(figure_context) == 18
    assert all(row["type"] == "thermodynamic_context" for row in figure_context)
    assert all(
        set(row["values"]) == {"quantity", "reason"}
        and row["values"]["quantity"] == "unscored_figure_context"
        and row["values"].get("reason")
        and row.get("attribution")
        for row in figure_context
    )
    assert all("point_conditions" not in row for row in figure_context)
    prose_context = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row["observation_id"].startswith("shornikov_2020_text_")
    }
    assert len(prose_context) == 8
    expected_text = {
        "shornikov_2020_text_delta_gamma_discrepancy": {
            "delta_gamma_FeO_as_printed": "~ 0.3",
            "FeO_mol_pct_as_printed": "25",
        },
        "shornikov_2020_text_calculation_temperature_range": {
            "calculation_temperature_range_K_as_printed": "1600–3200",
        },
        "shornikov_2020_text_mgfeo2_decomposition_bound": {
            "decomposition_temperature_upper_bound_K_as_printed": "1300",
            "decomposition_temperature_qualifier_as_printed": "do not exceed",
        },
        "shornikov_2020_text_mgfe2o3_decomposition_bound": {
            "decomposition_temperature_upper_bound_K_as_printed": "1300",
            "decomposition_temperature_qualifier_as_printed": "do not exceed",
        },
        "shornikov_2020_text_mixing_energy_minimum": {
            "minimum_at_2000_K_as_printed": "–10",
            "minimum_at_2773_K_as_printed": "–15",
        },
        "shornikov_2020_text_mixing_energy_comparison": {
            "Merli_comparison_temperature_K_as_printed": "2500",
            "Wu_comparison_temperature_K_as_printed": "2773",
            "maximum_difference_kJ_mol_as_printed": "2",
            "maximum_difference_qualifier_as_printed": "do not exceed",
        },
        "shornikov_2020_text_wu_near_ideal_range": {
            "temperature_range_K_as_printed": "2473–3073",
        },
        "shornikov_2020_text_present_study_ideal_range": {
            "FeO_concentration_range_mol_pct_as_printed": "80–100",
        },
    }
    for observation_id, fields in expected_text.items():
        assert {
            key: prose_context[observation_id]["values"][key]
            for key in fields
        } == fields
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 146
    assert sum(row.admission.status.value == "admitted" for row in observations) == 17
    assert sum(row.admission.status.value == "rejected" for row in observations) == 129
    assert sum(row.value.kind is ValueKind.POINT for row in observations) == 127
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 19
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
