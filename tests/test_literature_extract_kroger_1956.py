from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/kroger-kreitlow-1956-soda-lime-silicate-calorimetry.yaml"


def test_render_pins_and_printed_table_row_counts():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    table3 = observations["kroger_kreitlow_1956_nadisilicate_crystal_solution_table3"]
    assert len(table3["values"]["solution_heat_trials_as_printed"]) == 4
    assert table3["values"]["solution_heat_trials_as_printed"][0]["solution_heat_kcal_mol"] == -97.41

    table7_ids = [
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
    ]
    table7 = [observations[observation_id] for observation_id in table7_ids]
    assert len(table7) == 3
    direct_heats = [row["values"]["direct_heat_kcal_100kg"] for row in table7]
    assert direct_heats == [11780, 13300, 12260]
    assert table7[1]["values"]["direct_uncertainty_kcal_mol_glass"] == 0.1
    assert all(row["locator"]["page"] == 7 and row["locator"]["table"] == "7" for row in table7)

    table8 = observations["kroger_kreitlow_1956_table8_direct_glass_formation_runs"]
    assert len(table8["values"]["runs_as_printed"]) == 6
    assert table8["values"]["runs_as_printed"][5]["heat_cal_g_glass"] == 122.3

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["Na2O-CaO-SiO2"]["observations"]
        if row["observation_id"] == table7_ids[0]
    )
    row["values"]["direct_heat_kcal_100kg"] = 11781
    mutated_observations = {
        row["observation_id"]: row
        for species in mutated["species"].values()
        for row in species.get("observations", [])
    }
    mutated_heats = [
        mutated_observations[observation_id]["values"]["direct_heat_kcal_100kg"]
        for observation_id in table7_ids
    ]
    with pytest.raises(AssertionError):
        assert mutated_heats == [11780, 13300, 12260]


def test_isolated_migration_has_only_the_exact_quantity_schema_gaps():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(observations) == 23
    assert migrator.result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "unavailable" for row in observations.values()) == 17
    assert set(observations) == {
        row["observation_id"]
        for species in document["species"].values()
        for row in species.get("observations", [])
    }

    quantity_unknown = {
        observation_id
        for observation_id, row in observations.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == {
        "kroger_kreitlow_1956_soda_solution_table1",
        "kroger_kreitlow_1956_cao_solution_table2",
        "kroger_kreitlow_1956_nadisilicate_crystal_solution_table3",
        "kroger_kreitlow_1956_nadisilicate_glass_solution_table4",
        "kroger_kreitlow_1956_table5_crystalline_solution_and_devitrification_heats",
        "kroger_kreitlow_1956_table5_glass_solution_heats",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_6",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_4_8",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table8_direct_glass_formation_runs",
        "kroger_kreitlow_1956_batch_formation_reaction_heats_equations1_3",
        "kroger_kreitlow_1956_ternary_reaction_heats_from_binary_silicates_equations7_9",
        "kroger_kreitlow_1956_soda_dissociation_enthalpy_de_forcrand",
        "kroger_kreitlow_1956_quartz_solution_heat_roth_troitzsch",
    }
    assert not any(row.identity.species.phase.is_unknown for row in observations.values())
    assert all(row.identity.quantity.is_value for oid, row in observations.items() if oid not in quantity_unknown)
    for row in observations.values():
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
    for observation_id in (
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_6",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_4_8",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
    ):
        composition = observations[observation_id].identity.composition
        assert composition is not None and composition.is_value
