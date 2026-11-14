"""Render pins and isolated migration for Mavrogenes & O'Neill (1999)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SID = "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
SOURCE = f"{SID}::"

# Each run prints ΔIW, T, and P, not an absolute log. O'Neill (1988) eq. 11
# third segment is the 1-bar IW base for every run here (T > 1184 K):
# μ° = -551159 + 269.404 T - 16.9484 T ln(T) J/mol, then μ°/(R T ln 10)
# with R = 8.31446261815324. Frost (1991) Table 1 adds the IW molar-volume
# term 0.055 (P_bar - 1) / T, P_bar = printed kbar × 1000. The absolute log
# is that sum plus the printed ΔIW. All of these temperatures are above the
# 1450 K EMF window (predict-and-flag). J/mol over R T is dimensionless.
_MAVROGENES_LOG10_BAR = {
    "MAV1": Decimal("-11.101141516"),
    "MAV14": Decimal("-11.066780879"),
    "MAV2": Decimal("-10.762420242"),
    "MAV4": Decimal("-10.912420242"),
    "MAV9": Decimal("-10.752420242"),
    "MAV10": Decimal("-10.932420242"),
    "MAV5": Decimal("-10.353698968"),
    "MAV8": Decimal("-10.493698968"),
    "MAV12": Decimal("-10.833698968"),
    "MAV6": Decimal("-10.214977693"),
    "MAV7": Decimal("-10.274977693"),
    "MAV65": Decimal("-9.321141516"),
    "MAV64": Decimal("-8.982420242"),
    "MAV27": Decimal("-7.691141516"),
    "MAV26": Decimal("-7.362420242"),
    "MAV32": Decimal("-7.111408155"),
    "MAV29": Decimal("-6.771225711"),
    "MAV31": Decimal("-6.621043268"),
    "MAV54": Decimal("-5.342743641"),
    "MAV55": Decimal("-4.494204944"),
    "MAV45": Decimal("-3.757721090"),
    "MAV68": Decimal("-5.276853329"),
    "MAV69": Decimal("-4.806259788"),
    "MAV34": Decimal("-6.871408155"),
    "MAV36": Decimal("-6.371225711"),
    "MAV42": Decimal("-6.631225711"),
    "MAV58A": Decimal("-4.910095255"),
    "MAV52": Decimal("-4.454204944"),
}


def _assert_converted_delta_iw(identity, run_id: str) -> None:
    oxygen = identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert abs(
        oxygen.value.log10() - Decimal(5) - _MAVROGENES_LOG10_BAR[run_id]
    ) < Decimal("5e-9")


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document, table: str):
    return next(
        record
        for record in document["species"]["S"]["context"]
        if record["type"] == "table_context"
        and record["locator"]["table"] == table
    )


def _run_rows(document):
    return {
        row["values"]["run_id_as_printed"]: row
        for row in document["species"]["S"]["observations"]
    }


def _assert_uncertainty_locators_resolve_to_table_cells(document) -> None:
    table2_rows = {
        row["run_id"]: row
        for row in _context(document, "2")["values"]["rows_as_printed"]
    }
    observations = _run_rows(document)
    assert len(observations) == 28
    field_by_column = {7: ("S_ppm", "S (ppm)"), 8: ("observed_SD_ppm", "Obs. St. Dev.")}
    for run_id, raw in observations.items():
        uncertainty = raw["uncertainty"]
        locator = uncertainty["locator"]
        assert locator["page"] == 4
        assert locator["table"] == "2"
        assert locator["row_label"] == run_id
        assert locator["table_column_number"] in field_by_column
        field, header = field_by_column[locator["table_column_number"]]
        cell = table2_rows[run_id][field]
        assert locator["column"] == header
        assert locator["printed_cell"] == cell
        assert uncertainty["value"] == cell
        assert uncertainty["as_printed"] == cell


def _assert_render_pins(document) -> None:
    """Values below were checked against the rendered PDF pages 3, 4 and 6."""

    table1_record = _context(document, "1")
    table1 = table1_record["values"]
    assert table1["printed_row_count"] == 30
    assert table1["starting_material_row_count"] == 2
    assert table1["run_product_row_count"] == 28
    starting = {
        row["material_as_printed"]: row["oxide_wt_percent_as_printed"]
        for row in table1["starting_materials_as_printed"]
    }
    assert starting["Basalt"]["SiO2"] == "48.80"
    assert starting["Picrite"]["MgO"] == "17.20"
    mav1_product = next(
        row for row in table1["run_products_as_printed"]
        if row["run_id_as_printed"] == "MAV1"
    )
    assert mav1_product["oxide_wt_percent_as_printed"]["FeO(t)"] == "13.12"
    assert any(
        row["run_id_as_printed"] == "MACV26"
        for row in table1["run_products_as_printed"]
    )
    assert table1_record["locator"]["page"] == 3
    assert table1_record["locator"]["table"] == "1"

    table2_record = _context(document, "2")
    table2 = table2_record["values"]
    assert table2["printed_row_count"] == 28
    rows = {row["run_id"]: row for row in table2["rows_as_printed"]}
    assert rows["MAV1"]["S_ppm"] == "1120"
    assert rows["MAV1"]["pressure_kb"] == "5"
    assert rows["MAV1"]["temperature_C"] == "1400"
    assert rows["MAV1"]["delta_IW"] == "-1.56"
    assert rows["MAV1"]["observed_SD_ppm"] == "65"
    assert rows["MAV1"]["S_calc_ppm"] == "820"
    assert rows["MAV1"]["X_FeS"] == "ND"
    assert rows["MAV1"]["a_FeS_1_atm"] == "0.50"
    assert rows["MAV34"]["S_ppm"] == "4010"
    assert rows["MAV65"]["S_ppm"] == "1640"
    assert rows["MAV65"]["delta_IW"] == "+0.22"
    assert rows["MAV1"]["capsule"] == "$Fe_{100}$"
    assert rows["MAV26"]["capsule"] == "$Fe_{20}Ir_{80}$"
    assert table2_record["locator"]["page"] == 4
    assert table2_record["locator"]["table"] == "2"
    assert "Fe20Ir20" in table2["capsule_footnote_as_printed"]
    assert "Fe20Ir80" in table2["capsule_footnote_discrepancy"]

    table3_record = _context(document, "3")
    table3 = table3_record["values"]
    assert table3["printed_row_count"] == 8
    own_basalt = next(
        row for row in table3["rows_as_printed"]
        if row["fit_source_as_printed"] == "This study"
        and row["source_material_as_printed"] == "Synthetic basalt"
    )
    assert own_basalt["A"] == "-6684 (597)"
    assert own_basalt["B"] == "11.52 (0.34)"
    assert own_basalt["C"] == "-0.047 (0.004)"
    assert own_basalt["chi_square"] == "1.51"
    assert table3_record["locator"]["page"] == 6
    assert table3_record["locator"]["table"] == "3"
    fit_inputs = table3["input_rows_for_this_study_fits"]
    rows_by_id = _run_rows(document)
    basalt_ids = {
        raw["observation_id"] for raw in rows_by_id.values()
        if raw["values"]["starting_material_as_printed"] == "Basalt"
    }
    picrite_ids = {
        raw["observation_id"] for raw in rows_by_id.values()
        if raw["values"]["starting_material_as_printed"] == "Picrite"
    }
    assert set(fit_inputs["synthetic_basalt"]) == basalt_ids
    assert set(fit_inputs["synthetic_picrite"]) == picrite_ids

    by_run = _run_rows(document)
    mav1 = by_run["MAV1"]
    assert mav1["values"]["value"] == "1120"
    assert mav1["values"]["starting_composition_wt_pct_as_printed"]["SiO2"] == "48.80"
    assert mav1["values"]["composition_wt_pct"]["SiO2"] == "47.72"
    assert mav1["values"]["sulfur_speciation_as_printed"] == "not reported"
    assert mav1["locator"]["page"] == 4
    assert mav1["locator"]["table"] == "2"


def test_mavrogenes_1999_render_pins_cover_printed_tables_and_rows() -> None:
    document = _document()
    _assert_render_pins(document)
    assert len(document["species"]["S"]["observations"]) == 28
    assert len(document["species"]["S"]["context"]) == 10
    assert len(
        [
            record
            for record in document["species"]["S"]["context"]
            if record["type"] == "figure_record"
        ]
    ) == 5
    assert all(
        record["values"]["points"] == []
        for record in document["species"]["S"]["context"]
        if record["type"] == "figure_record"
    )


def test_mavrogenes_1999_render_pin_rejects_mutated_value() -> None:
    document = deepcopy(_document())
    _run_rows(document)["MAV1"]["values"]["value"] = "1121"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_mavrogenes_1999_uncertainty_locators_resolve_to_table_cells() -> None:
    _assert_uncertainty_locators_resolve_to_table_cells(_document())


def test_mavrogenes_1999_uncertainty_locator_rejects_column_mutation() -> None:
    document = deepcopy(_document())
    first_observation = document["species"]["S"]["observations"][0]
    first_observation["uncertainty"]["locator"]["table_column_number"] = 7
    with pytest.raises(AssertionError):
        _assert_uncertainty_locators_resolve_to_table_cells(document)


def test_mavrogenes_1999_isolated_migration_binds_conditions_and_identity() -> None:
    document = _document()
    _assert_render_pins(document)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    _assert_render_pins(document)
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    assert result.queue == []
    observations = {
        observation_id.removeprefix(SOURCE): observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(SOURCE)
    }
    assert len(observations) == 28
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in observations.values()
    ) == 0

    raw_rows = _run_rows(document)
    starting = {
        row["material_as_printed"]: row["oxide_wt_percent_as_printed"]
        for row in _context(document, "1")["values"]["starting_materials_as_printed"]
    }
    table2_rows = {
        row["run_id"]: row
        for row in _context(document, "2")["values"]["rows_as_printed"]
    }
    for run_id, raw in raw_rows.items():
        observation = observations[raw["observation_id"]]
        identity = observation.identity
        assert identity.quantity.is_value
        assert identity.quantity.value.value == "equilibrium_phase_composition"
        assert identity.phase_role.is_value
        assert identity.phase_role.value.value == "glass"
        assert identity.composition is not None and identity.composition.is_value
        assert identity.total_pressure_Pa is not None and identity.total_pressure_Pa.is_value
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        _assert_converted_delta_iw(identity, run_id)
        expected_starting = dict(starting[raw["values"]["starting_material_as_printed"]])
        expected_starting["FeOT"] = expected_starting.pop("FeO(t)")
        assert raw["values"]["starting_composition_wt_pct_as_printed"] == expected_starting
        assert raw["values"]["starting_composition_locator"]["table"] == "1"
        assert raw["values"]["starting_composition_locator"]["unit_as_printed"] == "wt%"
        assert raw["values"]["quench_statement"]["preservation"] == "not_stated"
        assert raw["values"]["quench_statement"]["locator"]["page"] == 4
        assert raw["values"]["coexisting_phases_as_printed"]["anhydrite"]["state"] == "not_reported"
        sulfide = raw["values"]["coexisting_phases_as_printed"]["sulfide"]
        printed = table2_rows[run_id]
        assert sulfide["X_FeS_as_printed"] == printed["X_FeS"]
        assert sulfide["X_FeS_typed_refusal"]["status"] == "typed_refused"
        assert sulfide["X_FeS_typed_refusal"]["phase_label_as_printed"] == "sulfide"
        refusal_locator = sulfide["X_FeS_typed_refusal"]["locator"]
        assert refusal_locator["page"] == 4
        assert refusal_locator["table"] == "2"
        assert refusal_locator["row_label"] == run_id
        assert refusal_locator["column"] == "X_FeS"
        assert refusal_locator["printed_cell"] == printed["X_FeS"]
        assert sulfide["a_FeS_as_printed"] == printed["a_FeS_1_atm"]
        assert sulfide["activity_method_class"] == "model_derived"

        conditions = observation.point_conditions
        for key in (
            "temperature_C",
            "temperature_K",
            "pressure_kbar",
            "delta_IW",
            "composition",
            "run_duration_h",
            "capsule",
        ):
            assert key in conditions, (run_id, key)
            assert conditions[key].locator is not None, (run_id, key)
        assert conditions["temperature_C"].state.value == int(raw["values"]["T_C"])
        assert conditions["temperature_C"].locator.table == "2"
        assert conditions["pressure_kbar"].state.value == int(raw["values"]["total_pressure_Pa"]["value"])
        assert conditions["pressure_kbar"].locator.table == "2"
        assert str(conditions["delta_IW"].state.value) == raw["values"]["log_fO2_as_delta_IW"].removeprefix("+")
        assert conditions["delta_IW"].locator.table == "2"
        assert raw["point_conditions"]["delta_IW"]["locator"]["buffer"] == "IW (iron-wüstite)"
        assert conditions["composition"].locator.table == "1"
        assert str(conditions["composition"].state.value["SiO2"]) == raw["values"]["composition_wt_pct"]["SiO2"]
        assert observation.value.point == int(raw["values"]["value"])
        assert "fO2_Pa" not in conditions
        assert "standard_pressure_Pa" not in conditions
        assert "ambient" not in conditions
        assert "room_temperature" not in conditions

    for run_id, expected_temperature, expected_pressure in (
        ("MAV1", "1673.15", "500000000"),
        ("MAV34", "1773.15", "500000000"),
        ("MAV69", "2073.15", "5500000000"),
    ):
        observation = observations[raw_rows[run_id]["observation_id"]]
        identity = observation.identity
        assert str(identity.temperature_K.value) == expected_temperature
        assert str(identity.total_pressure_Pa.value) == expected_pressure
        _assert_converted_delta_iw(identity, run_id)
