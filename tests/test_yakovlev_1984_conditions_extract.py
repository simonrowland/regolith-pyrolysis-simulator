"""Condition, table-cell, and figure-label pins for Yakovlev et al. (1984)."""

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-028-yakovlev-1984.yaml"
SOURCE_ID = "kems-028-yakovlev-1984"
SCHEMA_GAP_IDS = {
    "yakovlev_1984_CAI_Al_suboxide_fraction",
    "yakovlev_1984_CAI_Al2O_in_suboxide_fraction",
}
EXPECTED_QUANTITY_UNKNOWN_IDS = SCHEMA_GAP_IDS | {
    "yakovlev_1984_fig1_murchison_psat_figure_only",
    "yakovlev_1984_fig2_CAI_psat_figure_only",
    "yakovlev_1984_kems_method",
    "yakovlev_1984_murchison_Ca_max_1950_2025C",
    "yakovlev_1984_murchison_Fe_max_1450_1725C",
    "yakovlev_1984_murchison_Mg_max_1750_1825C",
    "yakovlev_1984_murchison_SiO_Mg_max_1750_1825C",
    "yakovlev_1984_murchison_TiO_gt_TiO2_at_Ca_max",
    "yakovlev_1984_sulfur_900_1000C",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _raw_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (species, observation)
        for species, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def _migrated_rows(migrator) -> dict[str, object]:
    return {
        row.observation_id: row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
    }


def test_conditions_reach_every_numeric_vapor_row_without_filling_unknown_axes() -> None:
    document = _document()
    raw = _raw_observations(document)
    numeric_alkali = {
        observation_id: observation["values"]["p_torr"]
        for observation_id, (species, observation) in raw.items()
        if species in {"Na", "K"}
        and "p_torr" in observation.get("values", {})
    }
    assert numeric_alkali == {
        "yakovlev_1984_murchison_Na_P_1225C": 0.00027,
        "yakovlev_1984_murchison_K_P_1225C": 4.2e-05,
    }

    partial_pressure_sources = [
        observation
        for _species, observation in raw.values()
        if observation.get("values", {}).get("quantity") == "partial_pressure"
    ]
    assert len(partial_pressure_sources) == 24
    for observation in partial_pressure_sources:
        conditions = observation["point_conditions"]
        for key in (
            "composition",
            "total_pressure_Pa",
            "total_pressure_kind",
            "fO2_log",
            "sample_label",
            "apparatus_family",
            "orifice_diameter_m",
        ):
            assert "state" in conditions[key]
            assert "locator" in conditions[key]
        assert conditions["composition"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_kind"]["state"]["tag"] == "unknown"
        assert conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert conditions["orifice_diameter_m"]["state"]["tag"] == "unknown"

    table = raw["yakovlev_1984_Po2_table_calculated"][1]["values"]["points"]
    assert len(table) == 11
    assert len({point["T_C"] for point in table}) == 7
    assert all("P" in point for point in table)

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    rows = _migrated_rows(migrator)
    assert len(rows) == 98
    assert len(migrator.result.validation.issues) == 261
    assert len(migrator.result.validation.hard_issues) == 0
    assert {
        observation_id
        for observation_id, row in rows.items()
        if row.identity.quantity.is_unknown
    } == {
        f"{SOURCE_ID}::{observation_id}"
        for observation_id in EXPECTED_QUANTITY_UNKNOWN_IDS
    }
    assert {
        observation_id
        for observation_id, (_species, observation) in raw.items()
        if observation.get("values", {}).get("quantity")
        == "Al_suboxide_pressure_fraction"
    } == SCHEMA_GAP_IDS
    assert all(
        rows[f"{SOURCE_ID}::{observation_id}"].identity.quantity.is_unknown
        for observation_id in SCHEMA_GAP_IDS
    )

    numeric_vapor_rows = [
        row
        for row in rows.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and row.value.kind is ValueKind.POINT
    ]
    assert numeric_vapor_rows
    for row in numeric_vapor_rows:
        assert row.identity.quantity.is_value
        assert row.identity.quantity.value is Quantity.P_PARTIAL
        conditions = row.point_conditions or {}
        for key in (
            "composition",
            "total_pressure_kind",
            "fO2_log",
            "sample_label",
            "apparatus_family",
            "orifice_diameter_m",
            "temperature_K",
        ):
            assert key in conditions, (row.observation_id, key)
            assert conditions[key].locator is not None, (row.observation_id, key)
            page = int(conditions[key].locator.published_page)
            assert page in {945, 946}
            assert conditions[key].locator.pdf_page_index == page - 945
        # The migrator intentionally drops an unprinted total from row-level
        # conditions; identity keeps its typed unknown state.
        assert "total_pressure_Pa" not in conditions
        assert conditions["composition"].state.is_unknown
        assert conditions["total_pressure_kind"].state.is_unknown
        assert conditions["fO2_log"].state.is_unknown
        assert conditions["orifice_diameter_m"].state.is_unknown
        assert conditions["apparatus_family"].state.value == "Knudsen effusion cell"
        assert conditions["temperature_K"].state.is_value
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown

    for observation_id in numeric_alkali:
        raw_phase = raw[observation_id][1]["phase"]
        assert raw_phase == "Murchison_CM_chondrite_melt_knudsen"
        row = next(
            row for row in rows.values() if row.observation_id.endswith(observation_id)
        )
        assert row.identity.species.phase.is_unknown
        assert raw_phase in (row.identity.species.phase.reason or "")


def test_render_fidelity_pins_prose_table_and_inline_figure_marker_series() -> None:
    document = _document()
    raw = _raw_observations(document)
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    for sample in document["fidelity_samples"]:
        locator = sample["locator"]
        page = int(locator["published_page"])
        assert page in {945, 946}
        assert locator["pdf_page_index"] == page - 945
    figure_samples = [
        sample
        for sample in document["fidelity_samples"]
        if sample["locator"].get("figure") in {"1", "2"}
    ]
    assert len(figure_samples) == 24
    assert all(
        sample["locator"]["published_page"] == 946
        and sample["locator"]["pdf_page_index"] == 1
        for sample in figure_samples
    )

    expected_figure_points = {
        "yakovlev_1984_fig1_Murchison_SiO_digitized": ("SiO(g)", "open circle", 1.3e-03, 8),
        "yakovlev_1984_fig1_Murchison_Mg_digitized": ("Mg(g)", "open square", 2.9e-05, 12),
        "yakovlev_1984_fig1_Murchison_Ca_digitized": ("Ca(g)", "filled square", 2.5e-04, 5),
        "yakovlev_1984_fig1_Murchison_Al_digitized": ("Al(g)", "open triangle", 1.4e-03, 3),
        "yakovlev_1984_fig2_CAI_SiO_digitized": ("SiO(g)", "open circle", 4.3e-04, 18),
        "yakovlev_1984_fig2_CAI_Mg_digitized": ("Mg(g)", "open square", 1.1e-04, 8),
        "yakovlev_1984_fig2_CAI_Ca_digitized": ("Ca(g)", "filled square", 7.0e-04, 10),
        "yakovlev_1984_fig2_CAI_Al_digitized": ("Al(g)", "open triangle", 9.7e-04, 5),
    }
    for observation_id, (species, observation) in raw.items():
        if observation_id not in expected_figure_points:
            continue
        expected_species, expected_marker, expected_pressure, expected_count = (
            expected_figure_points[observation_id]
        )
        points = observation["values"]["points"]
        point = points[0]
        assert observation["values"]["gas_species"] == expected_species
        assert len(points) == expected_count
        assert all(item["marker_symbol"] == expected_marker for item in points)
        assert point["marker_symbol"] == expected_marker
        assert point["P"] == expected_pressure
        assert point["point_id"].startswith("F")
    assert sum(item[3] for item in expected_figure_points.values()) == 69

    # The page-946 table has seven printed temperature rows and eleven filled
    # pressure cells. The typewriter I glyphs in the Murchison cells read as 1.
    points = raw["yakovlev_1984_Po2_table_calculated"][1]["values"]["points"]
    assert points[0]["P"] == 1.4e-06
    assert points[-1]["P"] == 1.9e-04

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["SiO"]["observations"]
        if row["observation_id"] == "yakovlev_1984_fig1_Murchison_SiO_digitized"
    )
    row["values"]["points"][0]["marker_symbol"] = "filled circle"
    assert check_all_fidelity_samples_match(mutated, label="mutated Yakovlev figure")

    mutated_pressure = deepcopy(document)
    row = next(
        row
        for row in mutated_pressure["species"]["SiO"]["observations"]
        if row["observation_id"] == "yakovlev_1984_fig1_Murchison_SiO_digitized"
    )
    row["values"]["points"][0]["P"] = 1.4e-03
    assert check_all_fidelity_samples_match(
        mutated_pressure, label="mutated Yakovlev figure pressure"
    )
