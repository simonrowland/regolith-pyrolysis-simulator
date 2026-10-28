from __future__ import annotations

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pickrell-1994-gaseous-alkali-corrosion.yaml"
EXPECTED_UNKNOWN_QUANTITIES = {
    "pickrell_1994_table1_si3n4_reaction_rate",
    "pickrell_1994_table1_si3n4_activation_energy",
    "pickrell_1994_table1_si3n4_silicate_layer_ratio",
    "pickrell_1994_table2_sic_reaction_rate",
    "pickrell_1994_table2_sic_activation_energy",
    "pickrell_1994_table2_sic_silicate_layer_ratio",
    "pickrell_1994_table3_alumina_product_analysis",
    "pickrell_1994_table3_alumina_soda_molar_ratio",
    "pickrell_1994_table4_beta_alumina_peak_area_measurements",
    "pickrell_1994_table4_beta_alumina_peak_area_ratio",
    "pickrell_1994_table4_beta_alumina_peak_area_ratio_after_26h_etch",
}


def test_pickrell_tables_migrate_with_exact_typed_quantity_gaps() -> None:
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
    assert len(rows) == 42
    unknown_quantity_ids = {
        observation_id.split("::", 2)[1]
        for observation_id, row in rows.items()
        if row.identity.quantity.is_unknown
    }
    assert unknown_quantity_ids == EXPECTED_UNKNOWN_QUANTITIES
    assert all(row.value.kind.value == "unavailable" for row in rows.values())
    assert all(row.identity.quantity.is_unknown for row in rows.values())
    assert all(not row.identity.species.phase.is_unknown for row in rows.values())

    si3n4 = document["species"]["Si3N4"]["observations"]
    sic = document["species"]["SiC"]["observations"]
    alumina = document["species"]["Al2O3"]["observations"]
    by_id = {row["observation_id"]: row for row in si3n4 + sic + alumina}
    assert len(by_id) == 11
    assert len(by_id["pickrell_1994_table1_si3n4_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_table2_sic_reaction_rate"]["values"]["points"]) == 4
    assert len(by_id["pickrell_1994_table3_alumina_product_analysis"]["values"]["points"]) == 7
    assert len(by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]["values"]["points"]) == 7
    assert len(by_id["pickrell_1994_table4_beta_alumina_peak_area_measurements"]["values"]["points"]) == 8
    assert len(by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio"]["values"]["points"]) == 7
    assert by_id["pickrell_1994_table1_si3n4_activation_energy"]["values"]["value"] == 199
    assert by_id["pickrell_1994_table1_si3n4_activation_energy"]["uncertainty"]["activation_energy_uncertainty_kJ_per_mol"] == 15
    assert by_id["pickrell_1994_table1_si3n4_silicate_layer_ratio"]["values"]["value"] == 2.22
    assert by_id["pickrell_1994_table2_sic_activation_energy"]["values"]["value"] == 104
    assert by_id["pickrell_1994_table2_sic_silicate_layer_ratio"]["values"]["value"] == 2.55

    # PDF p. 50, Table 1, row 4; verified against the rendered printed table.
    assert by_id["pickrell_1994_table1_si3n4_reaction_rate"]["values"]["points"][3]["value"] == 27.22
    # PDF p. 67, Table 2, row 3; verified against the rendered printed table.
    assert by_id["pickrell_1994_table2_sic_reaction_rate"]["values"]["points"][2]["value"] == 5.33
    # PDF p. 79, Table 3, row 4; both direct cells and the reduced ratio are pinned.
    assert by_id["pickrell_1994_table3_alumina_product_analysis"]["values"]["points"][3]["alumina_moles"] == 4.48
    assert by_id["pickrell_1994_table3_alumina_product_analysis"]["values"]["points"][3]["soda_moles"] == 1.14
    assert by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]["values"]["points"][3]["value"] == 3.94
    # PDF p. 81, Table 4, row 8: MinerU's malformed "Oren" is 0 by render.
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_measurements"]["values"]["points"][7]["beta_alumina_peak_area"] == 0
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_measurements"]["values"]["points"][7]["alumina_peak_area"] == "------"
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio_after_26h_etch"]["values"]["value"] == 0

    for observation in (
        by_id["pickrell_1994_table1_si3n4_reaction_rate"],
        by_id["pickrell_1994_table2_sic_reaction_rate"],
    ):
        for point in observation["values"]["points"]:
            assert "T_C" in point
            assert point["point_conditions"]["temperature_C"]["state"]["tag"] == "value"
            assert point["point_conditions"]["alkali_partial_pressure_Pa"]["state"]["tag"] == "unknown"
    assert by_id["pickrell_1994_table2_sic_reaction_rate"]["values"]["points"][0]["point_conditions"]["atmosphere"]["state"]["value"] == "dry air containing 0.98 vol% sodium nitrate vapors"

    rate_rows = [
        row
        for row in rows.values()
        if "_reaction_rate::T=" in row.observation_id
    ]
    assert len(rate_rows) == 8
    assert all(
        row.identity.temperature_K.is_value
        and row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator is not None
        and "total_pressure_Pa" not in row.point_conditions
        for row in rate_rows
    )
    sic_rows = [row for row in rate_rows if "table2_sic_reaction_rate::" in row.observation_id]
    assert len(sic_rows) == 4
    assert all(
        row.point_conditions["atmosphere"].state.value
        == "dry air containing 0.98 vol% sodium nitrate vapors"
        and row.point_conditions["atmosphere"].locator.page == 66
        for row in sic_rows
    )

    assert by_id["pickrell_1994_table1_si3n4_activation_energy"]["derived_from"] == [
        "pickrell_1994_table1_si3n4_reaction_rate"
    ]
    assert by_id["pickrell_1994_table2_sic_activation_energy"]["derived_from"] == [
        "pickrell_1994_table2_sic_reaction_rate"
    ]
    assert by_id["pickrell_1994_table3_alumina_soda_molar_ratio"]["derived_from"] == [
        "pickrell_1994_table3_alumina_product_analysis"
    ]
    assert by_id["pickrell_1994_table4_beta_alumina_peak_area_ratio"]["derived_from"] == [
        "pickrell_1994_table4_beta_alumina_peak_area_measurements"
    ]
