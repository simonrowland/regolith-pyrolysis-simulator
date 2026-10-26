from __future__ import annotations

from decimal import Decimal

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/mysen-kushiro-1988-condensation-evaporation-am-min.yaml"


def test_mysen_kushiro_1988_tables_and_migrated_percent_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    t1 = species["Ag"]["observations"][0]
    t2 = species["SiO2"]["observations"][0]
    t6 = next(
        observation for observation in species["Mg2SiO4"]["observations"]
        if observation["observation_id"].endswith("table6_forsterite_residue_oxide_compositions")
    )
    t5 = next(
        record for record in species["Mg2SiO4"]["context"]
        if record["observation_id"].endswith("table5_mg2sio4_thermochemical_fits_typed_refused")
    )
    t3 = next(
        record for block in species.values() for record in block["context"]
        if record["observation_id"].endswith("table3_silica_phase_compositions_typed_refused")
    )
    t4 = next(
        record for block in species.values() for record in block["context"]
        if record["observation_id"].endswith("table4_sio2_si_preliminary_runs_typed_refused")
    )
    t6_context = next(
        record for block in species.values() for record in block["context"]
        if record["observation_id"].endswith("table6_forsterite_totals_and_atomic_proportions")
    )
    t5_640 = next(row for row in t5["values"]["rows"] if row["delta_Hv_kJ_mol_as_printed"] == "640")

    assert len(t1["values"]["points"]) == 2
    assert "composition_mol" not in t1["values"]
    assert sum(
        len(observation["values"]["points"])
        for block in species.values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("mysen_kushiro_1988_table1_")
    ) == 3
    assert len(t2["values"]["points"]) + len(
        species["MgO"]["observations"][0]["values"]["points"]
    ) + len(species["Mg2SiO4"]["observations"][0]["values"]["points"]) == 63
    assert len(t6["values"]["points"]) == 9
    assert t1["values"]["points"][0]["mass_loss_pct"] == "29.3"
    assert t1["values"]["points"][0]["P_H2_as_printed"] == "1.0 × 10^-9 bar"
    assert t1["values"]["points"][0]["locator"]["page"] == 3
    assert t2["values"]["points"][0]["locator"]["table"] == "2"
    assert t2["values"]["points"][0]["P_H2_as_printed"] == "1.8 × 10^-9 bar"
    assert t6["values"]["points"][0]["SiO2_wt_pct"] == "41.56"
    assert t6["values"]["points"][0]["P_H2_as_printed"] == "1.8 × 10^-9 bar"
    assert t6["values"]["points"][0]["locator"]["page"] == 9
    assert len(t3["values"]["sample_conditions"]) == 4
    assert len(t3["values"]["weight_percent_rows"]) == 5
    assert len(t3["values"]["atomic_proportion_rows"]) == 4
    assert len(t4["values"]["rows"]) == 4

    table2_points = [
        point
        for observation in (
            t2,
            species["MgO"]["observations"][0],
            species["Mg2SiO4"]["observations"][0],
        )
        for point in observation["values"]["points"]
    ]
    no_h_rows = [
        point for point in table2_points
        if point.get("point_conditions", {}).get("pressure_medium_as_printed", {}).get("state", {}).get("value")
        == "No H"
    ]
    argon_rows = [
        point for point in table2_points
        if point.get("point_conditions", {}).get("pressure_medium_as_printed", {}).get("state", {}).get("value")
        == "Ar used as pressure medium"
    ]
    assert sorted(
        (point["point_conditions"]["pressure_medium_as_printed"]["locator"]["row"], point["T_C"])
        for point in no_h_rows
    ) == [(24, "1400"), (25, "1500"), (26, "1500"), (27, "1550"), (28, "1600"), (61, "1600")]
    assert all(point["P_H2_as_printed"] == "1.0 × 10^-9 ** (No H) bar" for point in no_h_rows)
    assert all(
        point["point_conditions"]["hydrogen_pressure_bar"]["state"]["tag"] == "unknown"
        and point["point_conditions"]["hydrogen_pressure_bar"]["state"]["reason"] == "not_applicable"
        and point["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        for point in no_h_rows
    )
    assert sorted(
        (point["point_conditions"]["pressure_medium_as_printed"]["locator"]["row"], point["T_C"])
        for point in argon_rows
    ) == [(11, "1618"), (13, "1640"), (16, "1661"), (62, "1540"), (63, "1600"), (64, "1650")]
    assert all(
        "*" in point["P_H2_as_printed"] and "**" not in point["P_H2_as_printed"]
        and point["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        for point in argon_rows
    )

    # PDF p. 6 render: single-star runs are sealed Pt; double-star runs use H2 in Mo Knudsen cells.
    table4_rows = t4["values"]["rows"]
    assert [
        (row["run_no_as_printed"], row["pressure_interpretation_as_printed"], row["pressure_bar_as_printed"])
        for row in table4_rows
    ] == [
        ("2882*", "Sealed Pt containers", "1.0 × 10^-8"),
        ("2883*", "Sealed Pt containers", "1.0 × 10^-8"),
        ("2892**", "Mo Knudsen cells with H2 as pressure medium", "9.0 × 10^-8"),
        ("2894**", "Mo Knudsen cells with H2 as pressure medium", "9.0 × 10^-8"),
    ]
    assert all(row["total_pressure_Pa"]["state"]["tag"] == "unknown" for row in table4_rows)
    assert [row["hydrogen_pressure_bar"]["state"]["tag"] for row in table4_rows] == [
        "unknown", "unknown", "value", "value"
    ]
    assert [row["hydrogen_pressure_bar"]["state"].get("value") for row in table4_rows] == [
        None, None, "9.0 × 10^-8 bar", "9.0 × 10^-8 bar"
    ]
    assert [row["locator"]["page"] for row in table4_rows] == [6, 6, 6, 6]
    assert [row["locator"]["table"] for row in table4_rows] == ["4", "4", "4", "4"]
    assert [row["locator"]["row"] for row in table4_rows] == [2, 3, 4, 5]
    assert [row["hydrogen_pressure_bar"]["locator"]["row"] for row in table4_rows] == [2, 3, 4, 5]
    assert "no H2 pressure is stated" in table4_rows[0]["hydrogen_pressure_bar"]["locator"]["note"]
    assert "H2 pressure printed" in table4_rows[2]["hydrogen_pressure_bar"]["locator"]["note"]
    assert len(t6_context["values"]["analytical_totals"]) == 3
    assert len(t6_context["values"]["atomic_proportions"]) == 3
    assert t5_640["delta_Hv_as_printed"] == "640 ± 36"
    assert t5_640["delta_Hv_uncertainty_kJ_mol_as_printed"] == "36"
    assert t5_640["secondary_reference_note"].startswith("Costa et al. (2017), Table 4")
    assert t5_640["locator"]["page"] == 7
    assert sum(
        len(record["values"]["rows"])
        for block in species.values()
        for record in block["context"]
        if record["observation_id"].startswith("mysen_kushiro_1988_table5_")
    ) == 8
    assert document["extraction"]["figure_read_candidates"][0]["figure"] == "3"
    gap = document["extraction"]["schema_gaps"][0]
    assert gap["locator"]["table"] == "1"
    assert gap["issue"].endswith("pure_oxide_system would mislabel).")
    assert "calibrated with liquid Cu and Ag" in gap["calibration_note"]
    assert gap["calibration_locator"]["page"] == 3

    migrator = Migrator(root=REPO_ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 75
    assert not migrator.result.validation.hard_issues
    migrated = list(migrator.result.observations.values())
    table1_rows = [
        row for row in migrated if "::mysen_kushiro_1988_table1_" in row.observation_id
    ]
    assert len(table1_rows) == 3
    assert all(row.identity.composition.is_unknown for row in table1_rows)
    assert all(
        row.point_conditions["composition"].state.is_value
        and row.point_conditions["composition"].state.value.basis == "printed_species_formula"
        for row in table1_rows
    )
    assert all(
        row.identity.composition.is_value
        for row in migrated
        if row not in table1_rows
    )
    assert all(row.identity.temperature_K.is_value for row in migrated)
    assert all(row.identity.total_pressure_Pa.tag.value == "unknown" for row in migrated)
    assert all(row.identity.fO2_Pa.tag.value == "unknown" for row in migrated)

    ag = next(
        row for row in migrator.result.observations.values()
        if "::mysen_kushiro_1988_table1_ag_mass_loss::T=1473.15:" in row.observation_id
    )
    assert ag.value.point == Decimal("0.293")
    assert ag.identity.temperature_K.value == Decimal("1473.15")
    assert ag.identity.total_pressure_Pa.tag.value == "unknown"
    assert ag.identity.fO2_Pa.tag.value == "unknown"

    assert not any("table5_row_" in row.observation_id for row in migrator.result.observations.values())
