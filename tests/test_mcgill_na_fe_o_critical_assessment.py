from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mcgill-na-fe-o-critical-assessment.yaml"
PDF_PAGE = 5


def _rows() -> dict[str, dict]:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    return {row["observation_id"]: row for row in doc["species"]["NaFeO2"]["observations"]}


def test_nafeo2_table_ii_render_values_and_comparison_are_pinned() -> None:
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    rows = _rows()
    assert len(rows) == 7
    pins = {
        "mcgill16_nafeo2_quoted_delta_fH": "-698.02",
        "mcgill16_nafeo2_quoted_entropy": "88.34",
        "mcgill16_nafeo2_assessed_delta_fH": "-697.350",
        "mcgill16_nafeo2_assessed_entropy": "88.341",
        "mcgill16_nafeo2_fusion_temperature_exp": "1620",
    }
    for observation_id, expected in pins.items():
        row = rows[observation_id]
        value_key = "T_m_K" if observation_id.endswith("fusion_temperature_exp") else "value"
        assert row["values"][value_key] == expected
        expected_page, expected_table = (7, "IV") if observation_id.endswith("fusion_temperature_exp") else (PDF_PAGE, "II")
        assert row["locator"]["page"] == expected_page
        assert row["locator"]["table"] == expected_table
    context = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))["species"]["NaFeO2"]["context"][0]
    transitions = context["values"]["polymorph_transition_enthalpies_as_printed"]
    assert [(row["transition"], row["value"], row["temperature_K"]) for row in transitions] == [
        ("alpha -> beta", "41.84", "870"),
        ("beta -> gamma", "2177", "1270"),
    ]
    assert context["values"]["optimized_thermochemistry_as_printed"] == {
        "formation_enthalpy_298_kJ_mol": "-697.350",
        "standard_entropy_298_J_mol_K": "88.341",
        "fusion_temperature_K": "1620",
    }

    source_contexts = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))["species"]["NaFeO2"]["context"]
    by_id = {row["observation_id"]: row for row in source_contexts}
    table_ii = by_id["mcgill16_table_ii_complete_thermochemistry_inventory"]
    assert table_ii["locator"]["page"] == 5 and table_ii["locator"]["table"] == "II"
    assert table_ii["values"]["printed_body_row_count"] == 32
    assert len(table_ii["values"]["rows"]) == 32
    table_ii_rows = table_ii["values"]["rows"]
    assert table_ii_rows[0] == {
        "species": "Na2O",
        "phase": "alpha",
        "delta_fH_kJ_mol": "-417.982",
        "S_J_mol_K": "75.061",
        "Cp": "66.216 + 0.0438651T - 813370.0184T^-2 - 1.40875003856E-5T^2 (298-1405 K)",
        "attribution": "[17]",
    }
    na4feo3_current = next(
        row for row in table_ii_rows
        if row.get("species") == "Na4FeO3" and row.get("delta_fH_kJ_mol") == "-1205.000"
    )
    assert na4feo3_current["S_J_mol_K"] == "212.625"
    assert table_ii_rows[-1] == {
        "species": "Na4FeO3",
        "delta_fH_kJ_mol": "-1205.000",
        "S_J_mol_K": "212.625",
        "Cp": "2 Cp(Na2O) + Cp(FeO)",
        "method_class": "model_derived",
        "attribution": "This work",
    }

    table_iii = by_id["mcgill16_table_iii_optimized_parameters"]
    assert table_iii["locator"]["page"] == 6 and table_iii["locator"]["table"] == "III"
    assert table_iii["values"]["printed_body_row_count"] == 25
    assert any("GNaFeO2 = 1/2GNa2O + 1/2GFe2O3 - 99371.2552 + 16.91756468T" in row
               for row in table_iii["values"]["parameters_as_printed"])

    table_iv = by_id["mcgill16_table_iv_na2o_fe2o3_invariants"]
    table_v = by_id["mcgill16_table_v_na2o_feo_invariants"]
    table_vi = by_id["mcgill16_table_vi_ternary_invariants"]
    assert table_iv["locator"]["page"] == 7 and table_iv["locator"]["table"] == "IV"
    assert table_v["locator"]["page"] == 12 and table_v["locator"]["table"] == "V"
    assert table_vi["locator"]["page"] == 14 and table_vi["locator"]["table"] == "VI"
    assert len(table_iv["values"]["rows"]) == table_iv["values"]["printed_body_row_count"] == 9
    assert len(table_v["values"]["rows"]) == table_v["values"]["printed_body_row_count"] == 9
    assert len(table_vi["values"]["rows"]) == table_vi["values"]["printed_body_row_count"] == 11
    assert table_iv["values"]["rows"][0]["T_exp"] == "1408 K (1135 °C) ± 24"
    assert table_iv["values"]["rows"][6]["Na2O_exp_mol_pct"] == "80.0"
    assert table_iv["values"]["rows"][6]["Na2O_calc_mol_pct"] == "80.0"
    assert table_v["values"]["rows"][0]["Na2O_exp_mol_pct"] == "25.00"
    assert table_vi["values"]["rows"][0]["FeO_mol_pct"] == "9.7"
    table_vi_1070 = next(row for row in table_vi["values"]["rows"] if row["T"] == "1070 K (797 °C)")
    assert (table_vi_1070["Na2O_mol_pct"], table_vi_1070["Fe2O3_mol_pct"], table_vi_1070["FeO_mol_pct"]) == ("51.8", "29.0", "45.3")
    assert sum(
        Decimal(table_vi_1070[key])
        for key in ("Na2O_mol_pct", "Fe2O3_mol_pct", "FeO_mol_pct")
    ) == Decimal("126.1")
    assert all(row["locator"]["page"] in {5, 6, 7, 12, 14} for row in by_id.values())


def test_isolated_migration_keeps_numeric_rows_and_transition_schema_gap() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 7
    assert not migrator.result.validation.hard_issues
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_delta_fH"
    ].value.point == Decimal("-698.02")
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_entropy"
    ].value.point == Decimal("88.34")
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_delta_fH"
    ].value.point == Decimal("-697.350")
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_entropy"
    ].value.point == Decimal("88.341")
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_fusion_temperature_exp"
    ].value.point == Decimal("1620")
    for observation_id in (
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_delta_fH",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_entropy",
    ):
        assert observations[observation_id].identity.standard_pressure_Pa.value == Decimal("101325")
    fusion = observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_fusion_temperature_exp"
    ]
    assert fusion.identity.total_pressure_Pa.is_unknown
    assert "total_pressure_Pa" not in fusion.point_conditions
    assert "fO2_log" not in fusion.point_conditions
    assert all(row.identity.species.phase.is_value for row in observations.values())
    for observation_id in (
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_delta_fH",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_quoted_entropy",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_delta_fH",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_entropy",
    ):
        assert observations[observation_id].identity.species.polymorph.value.value == "alpha"
    assert observations[
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_fusion_temperature_exp"
    ].identity.species.polymorph.value.value == "gamma"
    for observation_id in (
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_delta_fH",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_assessed_entropy",
    ):
        assert observations[observation_id].evidence.class_.value.value == "model_derived"
    assert {
        row_id for row_id, row in observations.items()
        if row.value.kind is ValueKind.UNAVAILABLE
    } == {
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_alpha_beta_transition_enthalpy",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_beta_gamma_transition_enthalpy",
    }
    assert {
        row_id for row_id, row in observations.items()
        if row.identity.quantity.is_unknown
    } == {
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_alpha_beta_transition_enthalpy",
        "mcgill-na-fe-o-critical-assessment::mcgill16_nafeo2_beta_gamma_transition_enthalpy",
    }
    assert all(
        row.point_conditions["temperature_K"].state.is_value
        for row in observations.values()
    )
