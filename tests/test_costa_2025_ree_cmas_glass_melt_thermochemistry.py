from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.identity import quantity_token
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT_NAME = "costa-2025-ree-cmas-glass-melt-thermochemistry.yaml"
SOURCE_ID = "costa-2025-ree-cmas-glass-melt-thermochemistry"
EXPECTED_HEAT_OF_FUSION = {
    "costa_2025_cmas_heat_of_fusion_table3": (Decimal("31.01"), Decimal("1493")),
    "costa_2025_y_l_heat_of_fusion_table3": (Decimal("18.54"), Decimal("1462")),
    "costa_2025_yb_l_heat_of_fusion_table3": (Decimal("23.44"), Decimal("1477")),
    "costa_2025_gd_l_heat_of_fusion_table3": (Decimal("23.31"), Decimal("1457")),
    "costa_2025_y_h_heat_of_fusion_table3": (Decimal("14.27"), Decimal("1462")),
    "costa_2025_yb_h_heat_of_fusion_table3": (Decimal("19.13"), Decimal("1454")),
    "costa_2025_gd_h_heat_of_fusion_table3": (Decimal("16.36"), Decimal("1475")),
}


def test_costa_2025_table3_values_compositions_and_migration(tmp_path: Path) -> None:
    extract_path = Path("data/literature/extracts") / EXTRACT_NAME
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    container = extract["species"]["CaO"]
    context = {row["observation_id"]: row for row in container["context"]}
    observations = {
        row["observation_id"]: row for row in container["observations"]
    }
    source_rows = {
        observation_id: row
        for observation_id, row in (context | observations).items()
        if row["locator"].get("table") == "3"
        and row["values"].get("quantity")
        in {"transition_temperature", "heat_of_fusion"}
    }
    assert set(observations) == set(EXPECTED_HEAT_OF_FUSION)

    # Table 3, PDF page 11: these literals were confirmed against its 600-dpi render.
    rendered_values = {
        "costa_2025_cmas_tg_table3": ("T_K", Decimal("1021")),
        "costa_2025_y_h_tc_table3": ("T_K", Decimal("1246")),
        "costa_2025_gd_h_heat_of_fusion_table3": (
            "delta_H_fus_kJ_mol-1",
            Decimal("16.36"),
        ),
    }
    for observation_id, (field, expected) in rendered_values.items():
        row = source_rows[observation_id]
        assert Decimal(str(row["values"][field])) == expected
        assert row["locator"]["pdf_page_index"] == 10
        assert row["locator"]["table"] == "3"

    assert len(source_rows) == 28
    composition_rows = {
        row["sample"]: row
        for row in context["costa_2025_table1_epma_glass_compositions"]["values"]["rows"]
    }
    assert len(composition_rows) == 7
    for row in source_rows.values():
        if row["values"]["quantity"] == "heat_of_fusion":
            assert row["type"] == "heat_capacity_and_heat_content_series"
            assert row["values"]["method_class"] == "measured_direct"
        else:
            assert row["type"] == "literature_context"
        composition = row["values"]["composition_mol"]
        assert composition["basis"] == "printed_mole_fraction"
        if row["values"]["quantity"] == "heat_of_fusion":
            assert row["values"]["sample_as_printed"] == row["locator"]["row"]
        else:
            assert row["values"]["composition_as_published"]
        assert composition == composition_rows[row["locator"]["row"]]["composition_mol"]
        assert "temperature_K" in row["point_conditions"]
        if row["locator"]["column"] in {"Tm (K)", "ΔHfusion (kJ·mol−1)"}:
            assert row["phase"] == "cr"
        if row["values"]["quantity"] == "transition_temperature":
            assert row["values"].get("property_kind")

    # Every glass's thermal sequence is Tg, then Tc, then Tm.
    column_order = {"Tg (K)": 0, "Tc (K)": 1, "Tm (K)": 2}
    for sample in composition_rows:
        transitions = sorted(
            (
                row
                for row in source_rows.values()
                if row["locator"]["row"] == sample
                and row["values"]["quantity"] == "transition_temperature"
            ),
            key=lambda row: column_order[row["locator"]["column"]],
        )
        assert [row["locator"]["column"] for row in transitions] == [
            "Tg (K)", "Tc (K)", "Tm (K)"
        ]
        assert [row["values"]["T_K"] for row in transitions] == sorted(
            row["values"]["T_K"] for row in transitions
        )

    # Table 1, PDF page 11: printed mol% values are held as mole fractions.
    assert composition_rows["CMAS"]["composition_mol"] == {
        "basis": "printed_mole_fraction",
        "CaO": 0.3234,
        "MgO": 0.1071,
        "Al2O3": 0.0762,
        "SiO2": 0.4933,
    }
    assert composition_rows["Y-H"]["composition_mol"]["Y2O3"] == 0.0493
    assert composition_rows["Gd-H"]["composition_mol"]["Gd2O3"] == 0.0502

    table4_rows = context["costa_2025_table4_drop_solution_enthalpies"]["values"]["rows"]
    assert len(table4_rows) == 14
    cmas_drop_solution = next(row for row in table4_rows if row["sample"] == "CMAS")
    assert cmas_drop_solution["value_kJ_mol-1"] == 45.76
    assert cmas_drop_solution["uncertainty_kJ_mol-1"] == 1.60
    assert cmas_drop_solution["experiment_count"] == 5
    assert next(row for row in table4_rows if row["sample"] == "CaO")["attribution"] == "[28]"

    table2 = context["costa_2025_table2_stoichiometric_coefficients"]["values"]
    assert len(table2["rows"]) == 7
    assert table2["rows"][0]["coefficients_as_printed"] == {
        "a": "0.3234(6)", "b": "0.1071(5)", "c": "0.1524(2)",
        "d": "—", "e": "0.4933(8)", "f": "1.646(2)",
    }
    table5 = context["costa_2025_table5_factsage_binary_oxide_enthalpies"]["values"]
    assert table5["method_class"] == "quoted_attributed"
    assert all(row["method_class"] == "quoted_attributed" for row in table5["rows"])
    table8 = context["costa_2025_table8_corrosion_reactions"]["values"]
    assert sum(len(group["equations_as_printed"]) for group in table8["rows"]) == 12
    assert table8["rows"][0]["equations_as_printed"][0] == (
        "Y2Si2O7 + 3.02275Ca0.3234Mg0.1071Al0.1524Si0.4933O1.646 = "
        "0.48878CaY4Si3O13 + 1.51137Ca0.3182Mg0.1054Al0.1502Y0.0297Si0.486O1.665 "
        "+ 0.00786CaO + 0.16444MgO + 0.11683Al2O3 + 1.29026SiO2"
    )
    table9 = context["costa_2025_table9_delta_fG"]["values"]
    assert table9["quantity"] == "delta_fG"
    assert len(table9["rows"]) == 20
    assert table9["rows"][0]["delta_fG_kJ_mol-1"] == -760.35
    assert table9["rows"][-1]["delta_fG_kJ_mol-1"] == -881.88
    table10 = context["costa_2025_table10_corrosion_delta_G"]["values"]
    assert table10["table_title_temperature_K"] == 1593
    assert table10["section_temperature_K"] == 1573
    assert sum(len(group["rows"]) for group in table10["groups"]) == 12
    assert table10["groups"][0]["rows"][0]["delta_G_corrosion_kJ_mol-1"] == -593.45
    assert table10["groups"][-1]["rows"][-1]["delta_G_corrosion_kJ_mol-1"] == -325.88

    table7_rows = context["costa_2025_table7_formation_enthalpies"]["values"]["rows"]
    assert len(table7_rows) == 7
    assert table7_rows[-1]["delta_Hf_oxide_kJ_mol-1"] == -13.81
    figures = [row for row in context.values() if row["type"] == "figure_record"]
    assert len(figures) == 5
    assert all(row["values"]["numeric_points"] == [] for row in figures)
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in figures
    )

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    migrated_costa_rows = [
        row for row in result.observations.values() if row.source_id == SOURCE_ID
    ]
    migrated_by_id = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrated_costa_rows
    }
    assert set(migrated_by_id) == set(EXPECTED_HEAT_OF_FUSION)
    for observation_id, (expected_enthalpy, expected_temperature) in (
        EXPECTED_HEAT_OF_FUSION.items()
    ):
        observation = migrated_by_id[observation_id]
        assert quantity_token(observation.identity) is Quantity.ENTHALPY_OF_FUSION
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected_enthalpy
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.value == expected_temperature
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_not_applicable
        assert (
            observation.identity.composition.reason
            == "profile heat_of_fusion does not use composition"
        )
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == expected_temperature
        assert temperature.locator is not None
        assert temperature.locator.page == 11
        assert temperature.locator.pdf_page_index == 10
        oxygen = observation.point_conditions["fO2_Pa"]
        assert oxygen.state.is_unknown
        assert oxygen.locator is not None
        assert oxygen.locator.page == 4
        assert oxygen.locator.pdf_page_index == 3
    assert not any(
        row.identity.species.formula == "CaO" and row.identity.composition.is_unknown
        for row in result.observations.values()
        if row.source_id == SOURCE_ID
    )
    assert not result.validation.hard_issues
