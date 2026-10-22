from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/khedim-abdelouhab-2011-cr2o3-soda-silicate-kinetics.yaml"


def test_render_transcriptions_and_mutations_for_tables_1_to_3():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = next(
        row
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_table1_glass_compositions"
    )
    table1_grid = [
        (
            row["glass_formula"],
            row["theoretical_Na2O_atomic_percent"],
            row["theoretical_SiO2_atomic_percent"],
            row["EPMA_Na2O_atomic_percent"],
            row["EPMA_Na2O_parenthetical"],
            row["EPMA_SiO2_atomic_percent"],
            row["EPMA_SiO2_parenthetical"],
            row["chemical_analysis_Na2O_atomic_percent"],
            row.get("chemical_analysis_Na2O_parenthetical"),
            row["chemical_analysis_SiO2_atomic_percent"],
            row.get("chemical_analysis_SiO2_parenthetical"),
        )
        for row in table1["values"]["compositions_as_printed"]
    ]
    assert table1_grid == [
        ("Na2O-1.5SiO2", "40.00", "60.00", "39.08", "0.25", "58.25", "0.92", "39.45", "0.19", "59.09", "0.52"),
        ("Na2O-2SiO2", "33.33", "66.67", "32.20", "0.46", "66.73", "0.39", "33.07", "0.22", "65.59", "0.61"),
        ("Na2O-2.5SiO2", "28.57", "71.43", "28.83", "0.52", "71.17", "0.22", "—", None, "—", None),
        ("Na2O-3SiO2", "25.00", "75.00", "24.21", "0.14", "77.44", "0.69", "—", None, "—", None),
        ("Na2O-3.5SiO2", "22.22", "77.78", "21.44", "0.66", "77.16", "0.89", "22.38", "0.28", "76.78", "0.49"),
        ("Na2O-4SiO2", "20.00", "80.00", "20.09", "0.09", "81.73", "0.58", "—", None, "—", None),
    ]

    table2 = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_table2_epma_program_comparison"
    )
    programs = table2["values"]["programs_as_printed"]
    table2_grid = (
        tuple(programs["P1"].values()),
        tuple(programs["P2"].values()),
        tuple(table2["values"]["elemental_results_at_percent"]["Na"]),
        tuple(table2["values"]["elemental_results_at_percent"]["Si"]),
        tuple(table2["values"]["elemental_results_at_percent"]["Cr"]),
        tuple(table2["values"]["oxide_results_wt_percent"]["Na2O"]),
        tuple(table2["values"]["oxide_results_wt_percent"]["SiO2"]),
        tuple(table2["values"]["oxide_results_wt_percent"]["Cr2O3"]),
    )
    expected_table2_grid = (
        ("10", "5", "5", "6", "3", "10", "5", "30", "15"),
        ("25", "10", "12", "6", "3", "10", "5", "30", "15"),
        ("19.27 ± 0.36", "20.65 ± 0.19"),
        ("23.44 ± 0.18", "22.76 ± 0.10"),
        ("0.26 ± 0.03", "0.250 ± 0.01"),
        ("28.89 ± 0.58", "31.39 ± 0.31"),
        ("68.14 ± 0.82", "67.09 ± 0.62"),
        ("0.97 ± 0.09", "0.93 ± 0.04"),
    )
    assert table2_grid == expected_table2_grid

    mutated_document = deepcopy(document)
    mutated_table2 = next(
        row
        for row in mutated_document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_table2_epma_program_comparison"
    )
    mutated_table2["values"]["elemental_results_at_percent"]["Cr"][0] = "0.27 ± 0.03"
    mutated_programs = mutated_table2["values"]["programs_as_printed"]
    mutated_table2_grid = (
        tuple(mutated_programs["P1"].values()),
        tuple(mutated_programs["P2"].values()),
        tuple(mutated_table2["values"]["elemental_results_at_percent"]["Na"]),
        tuple(mutated_table2["values"]["elemental_results_at_percent"]["Si"]),
        tuple(mutated_table2["values"]["elemental_results_at_percent"]["Cr"]),
        tuple(mutated_table2["values"]["oxide_results_wt_percent"]["Na2O"]),
        tuple(mutated_table2["values"]["oxide_results_wt_percent"]["SiO2"]),
        tuple(mutated_table2["values"]["oxide_results_wt_percent"]["Cr2O3"]),
    )
    with pytest.raises(AssertionError):
        assert mutated_table2_grid == expected_table2_grid
    mutated_expected_grid = list(expected_table2_grid)
    mutated_expected_grid[4] = ("0.27 ± 0.03", "0.250 ± 0.01")
    with pytest.raises(AssertionError):
        assert table2_grid == mutated_expected_grid

    table3 = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_table3_crvi_volatilisation_rates"
    )
    assert [
        (row["temperature_C"], row["rate"], row["parenthetical_width"])
        for row in table3["values"]["reported_values_as_printed"]
    ] == [
        ("993", "-1.52", "0.70"),
        ("1107", "-4.48", "1.44"),
        ("1207", "-5.80", "1.11"),
        ("1310", "-13.43", "2.73"),
    ]


def test_context_tables_and_deferred_figures_survive_isolated_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert set(observations) == {
        "khedim_abdelouhab_2011_reference3_chromia_saturation",
    }
    assert migrator.result.validation.hard_issues == ()

    quoted = observations["khedim_abdelouhab_2011_reference3_chromia_saturation"]
    assert quoted.value.kind.value == "point"
    assert quoted.value.point == Decimal("8.15")
    assert quoted.identity.quantity.value.value == "equilibrium_phase_composition"
    assert quoted.identity.species.formula == "Cr2O3"
    assert quoted.identity.species.phase.is_value
    assert quoted.identity.species.phase.value.value == "l"
    assert quoted.identity.temperature_K.is_value
    assert quoted.identity.temperature_K.value == Decimal("1573.15")
    assert quoted.identity.phase_role.is_value
    assert quoted.identity.phase_role.value.value == "melt"
    assert quoted.identity.subtype.is_value
    assert quoted.identity.subtype.value == "wt_percent"
    quoted_composition = quoted.point_conditions["composition"]
    assert quoted_composition.state.is_value
    assert dict(quoted_composition.state.value.components) == {
        "Na2O": Decimal("1"),
        "SiO2": Decimal("1.5"),
    }
    assert quoted_composition.locator.page == 10
    assert quoted.evidence.class_.value.value == "quoted_attributed"
    assert not quoted.notices
    assert quoted.locator.page == 10
    assert quoted.locator.pdf_page_index == 9

    figure_records = [
        row
        for species in document["species"].values()
        for row in species["context"]
        if row["type"] == "figure_record"
    ]
    records_with_locators = [
        row
        for species in document["species"].values()
        for row in [*species["observations"], *species["context"]]
    ]
    assert len(figure_records) == 18
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_records)
    assert all("points" not in row["values"] for row in figure_records)
    assert all(
        row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        for row in records_with_locators
    )
    table3 = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_table3_crvi_volatilisation_rates"
    )
    assert table3["values"]["reported_values_as_printed"][0]["temperature_C"] == "993"
    mass_balance = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_crvi_mass_balance"
    )
    assert [
        mass_balance["values"][key]
        for key in (
            "initial_Cr2O3_wt_percent",
            "final_melt_Cr2O3_wt_percent",
            "final_crystal_Cr2O3_wt_percent",
            "total_Cr2O3_volatilised_wt_percent",
        )
    ] == ["40", "10", "16", "14"]
    assert mass_balance["values"]["predicted_direct_liquid_loss_after_2h"] == {
        "value_as_published": "approximately 0.6",
        "units": "wt% Cr2O3",
        "approximate": True,
        "method_class": "model_derived",
    }
    transition_value = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_chromia_pyroxene_transition_temperature"
    )
    assert transition_value["values"]["temperature_C_as_printed"] == "1160"
    assert transition_value["values"]["uncertainty_as_printed"] == "±15"
    assert transition_value["locator"]["page"] == transition_value["locator"]["pdf_page_index"] + 1
    figure4_fit = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "khedim_abdelouhab_2011_figure4_printed_regression"
    )
    assert figure4_fit["values"]["equation_as_printed"] == "y = -2.3084x + 9.752"
