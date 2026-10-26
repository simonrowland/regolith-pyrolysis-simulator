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
    assert document["extraction"]["schema_gaps"][0]["locator"]["table"] == "1"

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
