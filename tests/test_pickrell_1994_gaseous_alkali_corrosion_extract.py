from __future__ import annotations

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pickrell-1994-gaseous-alkali-corrosion.yaml"
EXPECTED_QUANTITY_LABELS = {
    "pickrell_1994_table1_si3n4_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_table1_si3n4_activation_energy": "alkali_corrosion_activation_energy",
    "pickrell_1994_table1_si3n4_silicate_layer_ratio": "sodium_silicate_layer_SiO2_to_Na2O_molar_ratio",
    "pickrell_1994_table2_sic_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_table2_sic_activation_energy": "alkali_corrosion_activation_energy",
    "pickrell_1994_table2_sic_silicate_layer_ratio": "sodium_silicate_layer_SiO2_to_Na2O_molar_ratio",
    "pickrell_1994_table3_alumina_product_analysis": "etched_alumina_alkali_product_molar_amounts_and_ratio",
    "pickrell_1994_table3_alumina_soda_molar_ratio": "etched_alumina_to_soda_molar_ratio",
    "pickrell_1994_table4_beta_alumina_peak_area_measurements": "beta_alumina_and_alumina_xrd_peak_areas",
    "pickrell_1994_table4_beta_alumina_peak_area_ratio": "beta_alumina_to_alumina_xrd_peak_area_ratio",
    "pickrell_1994_table4_beta_alumina_peak_area_ratio_after_26h_etch": "beta_alumina_to_alumina_xrd_peak_area_ratio",
    "pickrell_1994_prose_alumina_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_prose_alumina_activation_energy": "alkali_corrosion_activation_energy",
    "pickrell_1994_prose_alumina_thickness_bound": "alumina_reaction_layer_thickness_upper_bound_um",
    "pickrell_1994_prose_mullite_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_prose_mullite_activation_energy": "alkali_corrosion_activation_energy",
    "pickrell_1994_prose_cordierite_linear_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_prose_cordierite_parabolic_reaction_rate": "reaction_layer_thickness_squared_rate_mm2_per_h",
    "pickrell_1994_prose_ttz_reaction_rate": "reaction_layer_thickness_growth_rate_um_per_h",
    "pickrell_1994_prose_ttz_activation_energy": "alkali_corrosion_activation_energy",
    "pickrell_1994_prose_ttz_thickness_after_15h": "reaction_layer_thickness_mm",
    "pickrell_1994_prose_ttz_zero_thickness_under_8h": "reaction_layer_thickness_below_8h_mm",
    "pickrell_1994_prose_ttz_alkali_free_blank_thickness_changes": "alkali_free_thermal_thickness_change_mm",
    "pickrell_1994_prose_aluminum_titanate_dimensional_change": "aluminum_titanate_dimensional_change_percent",
}
CAMPAIGN_ATMOSPHERE = (
    "air with approximately 1.0 +/- 0.1 vol% sodium nitrate vapor; "
    "Figure 10 caption reports 0.98 vol% for the same campaign"
)


def test_pickrell_extraction_migrates_with_exact_typed_quantity_gaps() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    rows = {
        row.observation_id: row
        for row in migrator.result.observations.values()
        if row.observation_id.startswith(f"{document['source_id']}::")
    }
    assert len(rows) == 66
    row_source_ids = {
        observation_id.split("::", 2)[1]
        for observation_id in rows
    }
    assert row_source_ids == set(EXPECTED_QUANTITY_LABELS)
    unknown_quantity_ids = {
        observation_id.split("::", 2)[1]
        for observation_id, row in rows.items()
        if row.identity.quantity.is_unknown
    }
    assert unknown_quantity_ids == set(EXPECTED_QUANTITY_LABELS)
    assert all(row.value.kind.value == "unavailable" for row in rows.values())
    assert all(row.identity.quantity.is_unknown for row in rows.values())
    assert all(not row.identity.species.phase.is_unknown for row in rows.values())
    for observation_id, quantity_label in EXPECTED_QUANTITY_LABELS.items():
        source_rows = [
            row
            for key, row in rows.items()
            if key.split("::", 2)[1] == observation_id
        ]
        assert source_rows
        assert all(
            row.identity.quantity.reason == f"unsupported quantity '{quantity_label}'"
            for row in source_rows
        )

    by_id = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species["observations"]
    }
    assert set(by_id) == set(EXPECTED_QUANTITY_LABELS)
    assert all(
        observation["values"]["quantity"] == EXPECTED_QUANTITY_LABELS[observation_id]
        for observation_id, observation in by_id.items()
    )

    # The machine-read tables and the prose values below were checked against the cited page renders.
    assert len(by_id["pickrell_1994_table1_si3n4_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_table2_sic_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_table3_alumina_product_analysis"]["values"]["points"]) == 7
    assert len(by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]["values"]["points"]) == 7
    assert len(by_id["pickrell_1994_table4_beta_alumina_peak_area_measurements"]["values"]["points"]) == 8
    assert len(by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio"]["values"]["points"]) == 7
    assert len(by_id["pickrell_1994_prose_alumina_reaction_rate"]["values"]["points"]) == 3
    assert len(by_id["pickrell_1994_prose_mullite_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_prose_cordierite_linear_reaction_rate"]["values"]["points"]) == 2
    assert len(by_id["pickrell_1994_prose_cordierite_parabolic_reaction_rate"]["values"]["points"]) == 2
    assert len(by_id["pickrell_1994_prose_ttz_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_prose_ttz_alkali_free_blank_thickness_changes"]["values"]["points"]) == 2

    # Table cells: rendered PDF page 50 Table 1 and page 67 Table 2.
    table1_last = by_id["pickrell_1994_table1_si3n4_reaction_rate"]["values"]["points"][3]
    assert table1_last["value"] == 27.22
    assert (table1_last["locator"]["page"], table1_last["locator"]["table"]) == (50, "1")
    table2_third = by_id["pickrell_1994_table2_sic_reaction_rate"]["values"]["points"][2]
    assert table2_third["value"] == 5.33
    assert (table2_third["locator"]["page"], table2_third["locator"]["table"]) == (67, "2")
    # Table 3 sibling columns remain explicit source cells; the printed ratio is not derived from rounded moles.
    table3_moles = by_id["pickrell_1994_table3_alumina_product_analysis"]["values"]["points"][3]
    assert table3_moles["alumina_moles"] == 4.48
    assert table3_moles["soda_moles"] == 1.14
    table3_ratio_row = by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]["values"]["points"][3]
    assert table3_ratio_row["value"] == 3.94
    assert (table3_ratio_row["locator"]["page"], table3_ratio_row["locator"]["table"]) == (79, "3")
    table3_ratio = by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]
    assert "derived_from" not in table3_ratio
    assert table3_ratio["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    assert table3_ratio["values"]["method_class"] == "measured_reduced"
    # Table 4 alumina peak area remains an explicit sibling; its final printed cell is a dash.
    table4_areas = by_id["pickrell_1994_table4_beta_alumina_peak_area_measurements"]["values"]["points"]
    assert table4_areas[7]["beta_alumina_peak_area"] == 0
    assert table4_areas[7]["alumina_peak_area"] == "------"
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio_after_26h_etch"]["values"]["value"] == 0

    # Prose kinetics and bounds: source PDF pages 75, 85 and 132.
    alumina_rates = by_id["pickrell_1994_prose_alumina_reaction_rate"]
    assert [(point["T_C"], point["value"]) for point in alumina_rates["values"]["points"]] == [
        (1050, 0.12),
        (1100, 0.24),
        (1150, 0.45),
    ]
    assert [point["n_exponent_as_printed"] for point in alumina_rates["values"]["points"]] == [0.9, 1.1, 1.3]
    assert [point["locator"]["page"] for point in alumina_rates["values"]["points"]] == [75, 75, 75]
    assert by_id["pickrell_1994_prose_alumina_activation_energy"]["values"]["value"] == 200
    assert by_id["pickrell_1994_prose_alumina_activation_energy"]["uncertainty"]["activation_energy_uncertainty_kJ_per_mol"] == 5
    alumina_bound = by_id["pickrell_1994_prose_alumina_thickness_bound"]["values"]["points"][0]
    assert (alumina_bound["T_C"], alumina_bound["exposure_time_h"], alumina_bound["value_operator"], alumina_bound["value"]) == (1050, 48, "<", 5)

    mullite_rates = by_id["pickrell_1994_prose_mullite_reaction_rate"]
    assert [(point["T_C"], point["value"]) for point in mullite_rates["values"]["points"]] == [
        (950, 6),
        (1000, 10),
        (1050, 25),
        (1100, 39),
    ]
    assert [point["n_exponent_as_printed"] for point in mullite_rates["values"]["points"]] == [0.7, 0.8, 1.2, 1.2]
    assert by_id["pickrell_1994_prose_mullite_activation_energy"]["values"]["value"] == 182
    assert by_id["pickrell_1994_prose_mullite_activation_energy"]["uncertainty"]["activation_energy_uncertainty_kJ_per_mol"] == 20

    cordierite_linear = by_id["pickrell_1994_prose_cordierite_linear_reaction_rate"]
    assert [(point["T_C"], point["value"]) for point in cordierite_linear["values"]["points"]] == [(950, 6), (1000, 9)]
    cordierite_parabolic = by_id["pickrell_1994_prose_cordierite_parabolic_reaction_rate"]
    assert cordierite_parabolic["units"] == "mm^2/h as printed"
    assert [(point["T_C"], point["value"]) for point in cordierite_parabolic["values"]["points"]] == [(1050, 0.0046), (1100, 0.0055)]
    assert [point["n_exponent_as_printed"] for point in cordierite_linear["values"]["points"]] == [0.8, 0.8]
    assert [point["n_exponent_as_printed"] for point in cordierite_parabolic["values"]["points"]] == [0.5, 0.4]
    cordierite_value_pages = {
        point["locator"]["page"]
        for series in (cordierite_linear, cordierite_parabolic)
        for point in series["values"]["points"]
    }
    assert cordierite_value_pages == {132}

    ttz_rates = by_id["pickrell_1994_prose_ttz_reaction_rate"]
    assert [(point["T_C"], point["value"]) for point in ttz_rates["values"]["points"]] == [
        (950, 0.4),
        (1000, 1.1),
        (1050, 3.4),
        (1100, 4.2),
    ]
    assert [point["n_exponent_as_printed"] for point in ttz_rates["values"]["points"]] == [3.0, 1.6, 1.1, 0.8]
    assert by_id["pickrell_1994_prose_ttz_activation_energy"]["values"]["value"] == 229
    ttz_thickness_observation = by_id["pickrell_1994_prose_ttz_thickness_after_15h"]
    ttz_thickness = ttz_thickness_observation["values"]
    assert (ttz_thickness["T_C"], ttz_thickness["exposure_time_h"], ttz_thickness["value"]) == (950, 15, 0.008)
    assert ttz_thickness_observation["point_conditions"]["atmosphere"]["state"]["value"] == CAMPAIGN_ATMOSPHERE
    ttz_zero_observation = by_id["pickrell_1994_prose_ttz_zero_thickness_under_8h"]
    ttz_zero = ttz_zero_observation["values"]
    assert (ttz_zero["exposure_time_operator"], ttz_zero["exposure_time_h"], ttz_zero["value"]) == ("<", 8, 0)
    assert "T_C" not in ttz_zero
    assert "temperature_C" not in ttz_zero_observation["point_conditions"]
    assert ttz_zero_observation["point_conditions"]["atmosphere"]["state"]["value"] == CAMPAIGN_ATMOSPHERE
    ttz_blanks = by_id["pickrell_1994_prose_ttz_alkali_free_blank_thickness_changes"]["values"]["points"]
    assert [point["value"] for point in ttz_blanks] == [0.007, 0.010]
    assert all(point["T_C"] == 1000 and point["exposure_time_h"] == 12 for point in ttz_blanks)
    assert all(point["point_conditions"]["atmosphere"]["state"]["value"] == "without alkalis present" for point in ttz_blanks)

    aluminum_titanate = by_id["pickrell_1994_prose_aluminum_titanate_dimensional_change"]
    assert aluminum_titanate["values"]["value"] == 25
    assert aluminum_titanate["units"] == "%"
    assert "not a rate" in aluminum_titanate["values"]["note"]
    assert aluminum_titanate["values"]["T_C"] == 950

    # These are measured-reduced fits. The plotted thickness-time values are not stored as parents.
    for observation_id, figure_page, figure in (
        ("pickrell_1994_table1_si3n4_reaction_rate", 51, "4"),
        ("pickrell_1994_table2_sic_reaction_rate", 66, "10"),
    ):
        observation = by_id[observation_id]
        assert observation["values"]["method_class"] == "measured_reduced"
        assert "derived_from" not in observation
        assert observation["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        assert observation["derivation"]["raw_inputs"]["locator"]["page"] == figure_page
        assert observation["derivation"]["raw_inputs"]["locator"]["figure"] == figure

    # One calculated campaign concentration, attached consistently to all alkali-exposed rate rows.
    campaign = document["extraction"]["campaign_atmosphere"]
    assert campaign["concentration_as_reported"] == "approximately 1.0 +/- 0.1 vol% NaNO3 vapor; Figure 10 caption states 0.98 vol%"
    assert "ideal gases" in campaign["derivation"]
    assert "weight-loss rate" in campaign["derivation"]
    assert "alkali_vapor_concentration_as_printed" not in str(document)
    rate_rows = [row for row in rows.values() if "_reaction_rate::T=" in row.observation_id]
    assert len(rate_rows) == 23
    assert all(
        (row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown)
        and "total_pressure_Pa" not in row.point_conditions
        for row in rate_rows
    )
    assert all(
        row.identity.temperature_K.is_value
        and row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator is not None
        for row in rate_rows
    )
    zero_rows = [
        row for row in rows.values()
        if row.observation_id.split("::", 2)[1] == "pickrell_1994_prose_ttz_zero_thickness_under_8h"
    ]
    assert len(zero_rows) == 1
    assert (
        zero_rows[0].identity.temperature_K is None
        or zero_rows[0].identity.temperature_K.is_unknown
    )
    assert zero_rows[0].point_conditions is not None
    assert "temperature_K" not in zero_rows[0].point_conditions
    assert zero_rows[0].point_conditions["atmosphere"].state.value == CAMPAIGN_ATMOSPHERE
    ttz_thickness_rows = [
        row for row in rows.values()
        if row.observation_id.split("::", 2)[1] == "pickrell_1994_prose_ttz_thickness_after_15h"
    ]
    assert len(ttz_thickness_rows) == 1
    assert ttz_thickness_rows[0].point_conditions["atmosphere"].state.value == CAMPAIGN_ATMOSPHERE
    assert ttz_thickness_rows[0].point_conditions["temperature_K"].state.is_value
    titanate_rows = [
        row for row in rows.values()
        if row.observation_id.split("::", 2)[1] == "pickrell_1994_prose_aluminum_titanate_dimensional_change"
    ]
    assert len(titanate_rows) == 1
    assert titanate_rows[0].point_conditions["temperature_K"].state.is_value
    assert titanate_rows[0].point_conditions["atmosphere"].state.value == CAMPAIGN_ATMOSPHERE
    assert all(
        row.point_conditions["atmosphere"].state.value == CAMPAIGN_ATMOSPHERE
        and row.point_conditions["atmosphere"].locator.page == 39
        for row in rate_rows
    )
    assert all(
        "alkali_partial_pressure_Pa" not in row.point_conditions
        or row.point_conditions["alkali_partial_pressure_Pa"].state.is_unknown
        for row in rate_rows
    )

    table1 = by_id["pickrell_1994_table1_si3n4_reaction_rate"]
    table2 = by_id["pickrell_1994_table2_sic_reaction_rate"]
    for observation in (table1, table2):
        assert observation["values"]["quantity"] == "reaction_layer_thickness_growth_rate_um_per_h"
        assert observation["values"]["method_class"] == "measured_reduced"
        assert all(
            point["point_conditions"]["atmosphere"]["state"]["value"] == CAMPAIGN_ATMOSPHERE
            for point in observation["values"]["points"]
        )
        assert all(
            point["point_conditions"]["atmosphere"]["locator"]["page"] == 39
            for point in observation["values"]["points"]
        )

    assert by_id["pickrell_1994_table1_si3n4_activation_energy"]["derived_from"] == [
        "pickrell_1994_table1_si3n4_reaction_rate"
    ]
    assert by_id["pickrell_1994_table2_sic_activation_energy"]["derived_from"] == [
        "pickrell_1994_table2_sic_reaction_rate"
    ]
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio"]["derived_from"] == [
        "pickrell_1994_table4_beta_alumina_peak_area_measurements"
    ]
