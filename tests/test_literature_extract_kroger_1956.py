from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from simulator.battery.enums import EvidenceClass

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/kroger-kreitlow-1956-soda-lime-silicate-calorimetry.yaml"
OXIDE_HEATS = {
    "kroger_kreitlow_1956_oxide_formation_naca2si3o8": -110.75,
    "kroger_kreitlow_1956_oxide_formation_na2ca3si3o8": -134.44,
    "kroger_kreitlow_1956_oxide_formation_na2ca6si6o13": -137.39,
}
ELEMENT_HEATS = {
    "kroger_kreitlow_1956_element_formation_naca2si3o8": -1141.95,
    "kroger_kreitlow_1956_element_formation_na2ca3si3o8": -1116.84,
    "kroger_kreitlow_1956_element_formation_na2ca6si6o13": -1945.19,
}


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
    table4 = observations["kroger_kreitlow_1956_nadisilicate_glass_solution_table4"]
    assert table4["values"]["mean_uncertainty_kcal_mol"] == 0.1

    assert {
        observation_id: observations[observation_id]["values"]["heat_of_formation_from_oxides_kcal_mol"]
        for observation_id in OXIDE_HEATS
    } == OXIDE_HEATS
    assert {
        observation_id: observations[observation_id]["values"]["delta_f_H"]
        for observation_id in ELEMENT_HEATS
    } == ELEMENT_HEATS
    assert "mean_uncertainty_kcal_mol" not in observations[
        "kroger_kreitlow_1956_element_formation_na2ca6si6o13"
    ]["values"]
    assert all(
        observations[observation_id]["values"]["quantity"] == "heat_of_formation_from_oxides"
        for observation_id in OXIDE_HEATS
    )
    assert all(
        observations[observation_id]["values"]["quantity"] == "delta_fH"
        for observation_id in ELEMENT_HEATS
    )

    devitrification_difference = observations[
        "kroger_kreitlow_1956_nadisilicate_devitrification_heat_solution_difference"
    ]
    direct_devitrification = observations[
        "kroger_kreitlow_1956_nadisilicate_devitrification_heat_direct_kroger_janetzko"
    ]
    assert devitrification_difference["values"]["devitrification_heat_from_solution_difference_kcal_mol"] == -8.79
    assert direct_devitrification["values"]["direct_devitrification_heat_kcal_mol"] == 8.81
    assert devitrification_difference["values"]["method_class"] == "measured_reduced"
    assert direct_devitrification["values"]["method_class"] == "quoted_attributed"

    disilicate_reaction = observations[
        "kroger_kreitlow_1956_nadisilicate_formation_reaction_heat_from_solution_heats"
    ]
    direct_disilicate_reaction = observations[
        "kroger_kreitlow_1956_nadisilicate_formation_reaction_heat_direct_kroger_janetzko"
    ]
    assert disilicate_reaction["values"]["formation_reaction_enthalpy_kcal_mol"] == 15.69
    assert direct_disilicate_reaction["values"]["direct_formation_reaction_enthalpy_kcal_mol"] == 16.5
    assert disilicate_reaction["values"]["method_class"] == "measured_reduced"
    assert direct_disilicate_reaction["values"]["method_class"] == "quoted_attributed"

    technical_glass_oxide_heats = {
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_6": -63.77,
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_4_8": -66.03,
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_0_71_4_85": -58.86,
    }
    assert {
        observation_id: observations[observation_id]["values"]["heat_of_formation_from_oxides_kcal_mol"]
        for observation_id in technical_glass_oxide_heats
    } == technical_glass_oxide_heats
    assert all(
        observations[observation_id]["values"]["method_class"] == "measured_reduced"
        for observation_id in technical_glass_oxide_heats
    )

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
    assert all(row["values"]["method_class"] == "measured_direct" for row in table7)

    solution_derived_ids = [
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_0_71_4_85",
    ]
    solution_derived = [observations[observation_id] for observation_id in solution_derived_ids]
    assert [row["values"]["solution_heat_kcal_100kg"] for row in solution_derived] == [11640, 13150, 12280]
    assert all(row["values"]["method_class"] == "measured_reduced" for row in solution_derived)

    prior_calculation_ids = [
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_6",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_4_8",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_0_71_4_85",
    ]
    prior_calculations = [observations[observation_id] for observation_id in prior_calculation_ids]
    assert [row["values"]["prior_calculation_kcal_100kg"] for row in prior_calculations] == [14859, 16502, 14841]
    assert all(row["values"]["method_class"] == "quoted_attributed" for row in prior_calculations)

    unnumbered_solution_ids = [
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_6",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_4_8",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_0_71_4_85",
    ]
    for observation_id in unnumbered_solution_ids:
        locator = observations[observation_id]["locator"]
        assert locator["page"] == 6 and locator["published_page"] == 398
        assert locator["section"] == "5" and "table" not in locator
        values = observations[observation_id]["values"]
        assert values["composition_wt_pct"]
        assert values["composition_locator"]["page"] == 7
        assert values["composition_locator"]["table"] == "7"

    table8 = observations["kroger_kreitlow_1956_table8_direct_glass_formation_runs"]
    assert len(table8["values"]["runs_as_printed"]) == 6
    assert table8["values"]["runs_as_printed"][5]["heat_cal_g_glass"] == 122.3


def test_isolated_migration_has_only_the_exact_quantity_schema_gaps():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(observations) == 36
    assert migrator.result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "unavailable" for row in observations.values()) == 33
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
        "kroger_kreitlow_1956_nadisilicate_devitrification_heat_solution_difference",
        "kroger_kreitlow_1956_nadisilicate_devitrification_heat_direct_kroger_janetzko",
        "kroger_kreitlow_1956_nadisilicate_formation_reaction_heat_from_solution_heats",
        "kroger_kreitlow_1956_nadisilicate_formation_reaction_heat_direct_kroger_janetzko",
        "kroger_kreitlow_1956_oxide_formation_naca2si3o8",
        "kroger_kreitlow_1956_oxide_formation_na2ca3si3o8",
        "kroger_kreitlow_1956_oxide_formation_na2ca6si6o13",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_6",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_4_8",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_6",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_6",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_4_8",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_0_71_4_85",
        "kroger_kreitlow_1956_table8_direct_glass_formation_runs",
        "kroger_kreitlow_1956_batch_formation_reaction_heats_equations1_3",
        "kroger_kreitlow_1956_ternary_reaction_heats_from_binary_silicates_equations7_9",
        "kroger_kreitlow_1956_soda_dissociation_enthalpy_de_forcrand",
        "kroger_kreitlow_1956_quartz_solution_heat_roth_troitzsch",
    }
    assert {
        observation_id
        for observation_id, row in observations.items()
        if row.value.kind.value == "point"
    } == set(ELEMENT_HEATS)
    expected_element_points = {
        "kroger_kreitlow_1956_element_formation_naca2si3o8": Decimal("-4777.91880"),
        "kroger_kreitlow_1956_element_formation_na2ca3si3o8": Decimal("-4672.85856"),
        "kroger_kreitlow_1956_element_formation_na2ca6si6o13": Decimal("-8138.67496"),
    }
    assert {
        observation_id: observations[observation_id].value.point
        for observation_id in ELEMENT_HEATS
    } == expected_element_points
    assert all(observations[observation_id].identity.quantity.is_unknown for observation_id in OXIDE_HEATS)
    assert all(observations[observation_id].value.kind.value == "unavailable" for observation_id in OXIDE_HEATS)
    assert all(
        observations[observation_id].evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for observation_id in (
            "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
            "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
            "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
        )
    )
    assert all(
        observations[observation_id].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for observation_id in (
            "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_6",
            "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_4_8",
            "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_0_71_4_85",
        )
    )
    assert all(
        observations[observation_id].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        for observation_id in (
            "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_6",
            "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_4_8",
            "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_0_71_4_85",
        )
    )
    assert not any(row.identity.species.phase.is_unknown for row in observations.values())
    assert all(row.identity.quantity.is_value for oid, row in observations.items() if oid not in quantity_unknown)
    for row in observations.values():
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
    for observation_id in (
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_6",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_1_4_8",
        "kroger_kreitlow_1956_table6_technical_glass_solution_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_6",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_technical_glass_oxide_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_glass_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_6",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_1_4_8",
        "kroger_kreitlow_1956_table7_solution_derived_formation_heat_1_0_71_4_85",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_6",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_1_4_8",
        "kroger_kreitlow_1956_table7_prior_formation_heat_reference_23_1_0_71_4_85",
    ):
        composition = observations[observation_id].identity.composition
        assert composition is not None and composition.is_value
