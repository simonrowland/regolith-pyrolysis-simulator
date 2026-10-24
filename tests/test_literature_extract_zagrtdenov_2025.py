from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate
from tests.helpers.literature_extract import find_context_row

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/zagrtdenov-2025-chromite-magnesiochromite-dataset.yaml"

TABLE1 = [
    ("S2 chromite", "31", (None, "14.81±0.34", "26.45±0.42", None, None, None, None, "0.10±0.02", "41.15±0.27", "0.21±0.04", "15.87±0.62", "0.16±0.05", "98.75", None)),
    ("Cr2O3", "19", (None, "0.28±0.28", "0.14±0.01", None, None, None, None, "1.45±0.03", "97.92±1.19", None, None, None, "99.79", None)),
    ("MORB 3786/3 *", None, ("3.04", "8.21", "15.31", "50.31", "0.18", "0.31", "10.29", "1.45", "0.04", "0.17", "8.91", None, "98.22", "275")),
    ("Di63An37", "15", (None, "11.30±0.19", "15.22±0.25", "50.52±0.35", None, None, "22.95±0.31", None, None, None, None, None, "99.99", None)),
]
TABLE1_COLUMNS = ("Na2O", "MgO", "Al2O3", "SiO2", "P2O5", "K2O", "CaO", "TiO2", "Cr2O3", "MnO", "FeO", "NiO", "Total", "Cr ppm")

TABLE2 = [
    ("E23", "1440", "24", "Chromite – MORB melt", "FMQ-2"),
    ("E19", "1440", "24", "Chromite – MORB melt", "FMQ"),
    ("E27", "1440", "24", "Chromite – MORB melt", "FMQ+2"),
    ("E24", "1440", "24", "Chromite – haplobasaltic (Di63An37) melt", "FMQ-2"),
    ("E32", "1440", "24", "Chromite – haplobasaltic (Di63An37) melt", "FMQ"),
    ("E28", "1440", "24", "Chromite – haplobasaltic (Di63An37) melt", "FMQ+2"),
    ("E25", "1440", "24", "Cr2O3 – MORB melt", "FMQ-2"),
    ("E31", "1440", "24", "Cr2O3 – MORB melt", "FMQ"),
    ("E29", "1440", "24", "Cr2O3 – MORB melt", "FMQ+2"),
    ("E26", "1440", "24", "Cr2O3 – haplobasaltic (Di63An37) melt", "FMQ-2"),
    ("E33", "1440", "24", "Cr2O3 – haplobasaltic (Di63An37) melt", "FMQ"),
    ("E30", "1440", "24", "Cr2O3 – haplobasaltic (Di63An37) melt", "FMQ+2"),
]

TABLE3_COLUMNS = ("MgO", "Al2O3", "TiO2", "V2O3", "Cr2O3", "MnO", "FeO", "Total")
TABLE3 = [
    ("E25", "2", ("13.06±0.32", "9.37±1.97", "0.36±0.01", "0.19±0.01", "61.22±1.98", "0.27±0.05", "13.27±0.25", "97.74")),
    ("E26", "3", ("20.14±0.07", "4.77±0.70", "D.L.", "D.L.", "71.78±0.70", "D.L.", "1.28±0.04", "97.97")),
    ("E27", "3", ("15.95±0.53", "21.49±2.73", "0.40±0.06", "D.L.", "42.73±3.16", "0.26±0.05", "16.91±0.28", "97.74")),
    ("E29", "5", ("D.L.", "0.22±0.03", "1.55±0.58", "D.L.", "95.32±2.69", "D.L.", "1.08±0.75", "98.17")),
    ("E30", "14", ("D.L.", "D.L.", "1.11±0.53", "D.L.", "98.13±1.39", "D.L.", "0.20±0.08", "99.44")),
    ("E31", "12", ("D.L.", "D.L.", "1.53±0.30", "D.L.", "97.48±0.90", "D.L.", "0.64±0.28", "99.65")),
    ("E33", "19", ("D.L.", "D.L.", "1.45±0.30", "D.L.", "97.92±1.19", "D.L.", "D.L.", "99.37")),
]

TABLE4_COLUMNS = ("MgO", "Al2O3", "TiO2", "Cr2O3", "MnO", "FeO")
TABLE4_FORMULA = ("Mg", "Al", "Ti", "Cr", "Mn", "Fe2+", "Fe3+", "O")
TABLE4 = [
    ("E19", "5", "Chr", ("13.07±0.33", "16.24±1.68", "0.41±0.01", "51.53±2.17", "0.24±0.04", "16.82±0.12"), "98.31", "153.6", ("0.62", "0.61", "0.01", "1.30", "0.01", "0.38", "0.07", "4")),
    ("E23", "5", "Chr", ("14.47±0.16", "14.71±1.09", "0.39±0.02", "55.42±1.03", "0.29±0.04", "12.04±0.19"), "97.32", "76.9", ("0.69", "0.56", "0.01", "1.41", "0.01", "0.31", "0.02", "4")),
    ("E24", "5", "Mchr", ("20.60±0.21", "11.45±1.65", "D.L.", "63.78±1.84", "D.L.", "1.73±0.07"), "97.56", "89.7", ("0.96", "0.42", "0.00", "1.57", "0.00", "0.04", "0.00", "4")),
    ("E25", "5", "Chr", ("13.47±0.23", "11.65±0.49", "0.41±0.01", "58.67±0.51", "0.30±0.03", "13.25±0.12"), "97.75", "80.2", ("0.66", "0.45", "0.01", "1.52", "0.01", "0.35", "0.02", "4")),
    ("E26", "5", "Mchr", ("20.48±0.20", "7.72±0.72", "D.L.", "68.16±0.92", "D.L.", "1.25±0.03"), "97.61", "85.8", ("0.97", "0.29", "0.00", "1.71", "0.00", "0.03", "0.00", "4")),
    ("E27", "13", "Chr", ("14.67±0.85", "14.73±1.38", "0.35±0.03", "51.70±1.63", "0.27±0.03", "16.34±0.23"), "98.06", "222.7", ("0.70", "0.55", "0.01", "1.30", "0.01", "0.31", "0.13", "4")),
    ("E28", "11", "Mchr", ("21.22±0.37", "9.74±0.19", "D.L.", "65.97±0.67", "D.L.", "1.07±0.05"), "98.00", "170.3", ("0.99", "0.36", "0.00", "1.63", "0.00", "0.01", "0.01", "4")),
    ("E29", "6", "Chr", ("11.89±0.35", "8.42±1.03", "0.35±0.02", "56.13±2.33", "0.27±0.05", "20.70±0.47"), "97.76", "254.4", ("0.59", "0.33", "0.01", "1.48", "0.01", "0.41", "0.17", "4")),
    ("E30", "6", "Mchr", ("19.70±0.17", "5.99±1.25", "D.L.", "68.57±1.68", "D.L.", "3.10±0.18"), "97.36", "169.5", ("0.94", "0.23", "0.00", "1.74", "0.00", "0.06", "0.03", "4")),
    ("E31", "5", "Chr", ("11.60±0.31", "8.65±1.97", "0.40±0.01", "60.34±2.90", "0.26±0.02", "17.01±0.16"), "98.26", "176.4", ("0.58", "0.34", "0.01", "1.59", "0.01", "0.43", "0.05", "4")),
    ("E33", "6", "Mchr", ("20.90±0.37", "6.44±1.89", "D.L.", "69.95±2.20", "D.L.", "D.L."), "97.29", "134.9", ("0.99", "0.24", "0.00", "1.76", "0.00", "0.00", "0.00", "4")),
]

TABLE5_COLUMNS = ("Na2O", "MgO", "Al2O3", "SiO2", "K2O", "CaO", "TiO2", "Cr2O3", "MnO", "FeO")
TABLE5 = [
    ("E19", "71", ("1.02±0.04", "8.60±0.08", "15.89±0.08", "52.09±0.19", "0.23±0.02", "10.67±0.11", "1.50±0.03", "0.420±0.005", "0.17±0.05", "8.91±0.13"), "99.50", "2874±37", "0.590"),
    ("E23", "50", ("0.51±0.03", "8.98±0.09", "16.54±0.10", "53.96±0.22", "0.19±0.02", "10.93±0.1", "1.57±0.03", "0.922±0.008", "0.18±0.03", "6.04±0.09"), "99.82", "6308±54", "0.581"),
    ("E24", "50", ("D.L.", "15.03±0.11", "9.21±0.07", "49.77±0.20", "D.L.", "24.66±0.2", "D.L.", "0.880±0.008", "D.L.", "1.04±0.04"), "100.59", "6020±57", "0.619"),
    ("E25", "50", ("0.60±0.03", "8.31±0.08", "15.90±0.10", "54.66±0.17", "0.20±0.02", "11.08±0.1", "1.63±0.03", "0.938±0.008", "0.16±0.03", "6.27±0.11"), "99.75", "6418±53", "0.581"),
    ("E26", "49", ("D.L.", "14.47±0.12", "8.46±0.08", "50.61±0.17", "D.L.", "25.17±0.17", "D.L.", "0.997±0.008", "D.L.", "0.68±0.04"), "100.39", "6821±56", "0.617"),
    ("E27", "70", ("1.84±0.04", "8.63±0.09", "16.07±0.11", "52.75±0.21", "0.27±0.02", "10.76±0.1", "1.52±0.02", "0.292±0.005", "0.17±0.03", "7.19±0.11"), "99.49", "1998±33", "0.588"),
    ("E28", "71", ("0.20±0.02", "14.93±0.11", "9.10±0.08", "50.20±0.18", "D.L.", "25.04±0.15", "D.L.", "0.480±0.007", "D.L.", "0.52±0.04"), "100.47", "3284±51", "0.618"),
    ("E29", "63", ("2.16±0.05", "7.82±0.07", "15.03±0.09", "51.66±0.19", "0.27±0.02", "10.60±0.1", "1.54±0.03", "0.287±0.006", "0.16±0.04", "9.73±0.11"), "99.26", "1964±42", "0.594"),
    ("E30", "64", ("0.13±0.02", "14.24±0.08", "8.37±0.08", "50.26±0.20", "D.L.", "25.16±0.16", "D.L.", "0.513±0.006", "D.L.", "1.59±0.06"), "100.26", "3510±41", "0.619"),
    ("E31", "77", ("1.25±0.04", "8.00±0.08", "15.39±0.10", "52.94±0.14", "0.25±0.02", "10.79±0.11", "1.58±0.03", "0.447±0.007", "0.17±0.03", "8.56±0.12"), "99.38", "3058±45", "0.588"),
    ("E32", "50", ("0.11±0.02", "15.11±0.13", "9.18±0.07", "50.22±0.19", "D.L.", "25.10±0.14", "D.L.", "0.598±0.007", "D.L.", "0.22±0.03"), "100.54", "4095±45", "0.618"),
    ("E33", "50", ("D.L.", "14.55±0.13", "8.55±0.06", "51.15±0.21", "D.L.", "25.55±0.18", "D.L.", "0.657±0.005", "D.L.", "D.L."), "100.46", "4495±32", "0.616"),
]


def _table1_grid(document):
    rows = find_context_row(document, "zagrtdenov_2025_table1_starting_material_compositions")["values"]["rows_as_printed"]
    return [(row["starting_material_as_printed"], row["number_of_analyses_as_printed"], tuple(row["cells_as_printed"][name] for name in TABLE1_COLUMNS)) for row in rows]


def _table2_grid(document):
    rows = find_context_row(document, "zagrtdenov_2025_table2_conditions")["values"]["rows_as_printed"]
    return [(row["run_as_printed"], row["temperature_C_as_printed"], row["duration_hours_as_printed"], row["system_as_printed"], row["buffer_as_printed"]) for row in rows]


def _table3_grid(document):
    rows = find_context_row(document, "zagrtdenov_2025_table3_crystal_centers")["values"]["rows_as_printed"]
    return [(row["run_as_printed"], row["number_of_analyses_as_printed"], tuple(row["cells_as_printed"][name] for name in TABLE3_COLUMNS)) for row in rows]


def _table4_grid(document):
    rows = find_context_row(document, "zagrtdenov_2025_table4_crystal_rims")["values"]["rows_as_printed"]
    return [(row["run_as_printed"], row["number_of_analyses_as_printed"], row["phase_as_printed"], tuple(row["composition_wt_percent_as_printed"][name] for name in TABLE4_COLUMNS), row["total_wt_percent_as_printed"], row["D_Cr_cryst_glass_as_displayed"], tuple(row["spinel_formula_AB2O4_recalculation_as_displayed"][name] for name in TABLE4_FORMULA)) for row in rows]


def _table5_grid(document):
    rows = find_context_row(document, "zagrtdenov_2025_table5_glass")["values"]["rows_as_printed"]
    return [(row["sample_as_printed"], row["number_of_analyses_as_printed"], tuple(row["composition_wt_percent_as_printed"][name] for name in TABLE5_COLUMNS), row["total_wt_percent_as_printed"], row["Cr_ppm_as_printed"], row["optical_basicity_as_displayed"]) for row in rows]


def test_dataset_workbook_tables_are_bound_to_the_printed_rows_and_mutation_checked():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _table1_grid(document) == TABLE1
    assert _table2_grid(document) == TABLE2
    assert _table3_grid(document) == TABLE3
    assert _table4_grid(document) == TABLE4
    assert _table5_grid(document) == TABLE5

    expected = list(TABLE5)
    row = list(expected[1])
    cells = list(row[2])
    cells[7] = "0.922±0.080"
    expected[1] = (*row[:2], tuple(cells), *row[3:])
    with pytest.raises(AssertionError):
        assert _table5_grid(document) == expected

    changed = deepcopy(document)
    table5 = find_context_row(changed, "zagrtdenov_2025_table5_glass")["values"]["rows_as_printed"]
    table5[1]["composition_wt_percent_as_printed"]["Cr2O3"] = "0.922±0.080"
    with pytest.raises(AssertionError):
        assert _table5_grid(changed) == TABLE5

    expected_table4 = list(TABLE4)
    row = list(expected_table4[0])
    components = list(row[3])
    components[0] = "13.70±0.33"
    expected_table4[0] = (*row[:3], tuple(components), *row[4:])
    with pytest.raises(AssertionError):
        assert _table4_grid(document) == expected_table4

    changed_table4 = deepcopy(document)
    table4 = find_context_row(changed_table4, "zagrtdenov_2025_table4_crystal_rims")["values"]["rows_as_printed"]
    table4[0]["composition_wt_percent_as_printed"]["MgO"] = "13.70±0.33"
    with pytest.raises(AssertionError):
        assert _table4_grid(changed_table4) == TABLE4


def test_dataset_separates_own_runs_compilations_and_duplicate_2020_measurements():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["Cr"]["context"]
    table5 = find_context_row(document, "zagrtdenov_2025_table5_glass")["values"]["rows_as_printed"]
    assert len(table5) == 12
    assert table5[1]["related_observation_ids"]["Cr2O3"] == "zagrtdenov_2020_E23_cr2o3"
    assert table5[1]["Cr_ppm_as_printed"] == "6308±54"
    assert table5[10]["Cr_ppm_as_printed"] == "4095±45"
    inventory = find_context_row(document, "zagrtdenov_2025_input_output_workbooks")["values"]
    assert inventory["this_study_runs"] == ["E19", "E23", "E24", "E25", "E26", "E27", "E28", "E29", "E30", "E31", "E33"]
    assert inventory["compiled_literature_row_count"] == 214
    natural = find_context_row(document, "zagrtdenov_2025_compiled_natural_glass_data")["values"]
    assert natural["compiled_csv_data_rows_total"] == 35267
    assert natural["natural_glass_data_rows"] == 91200
    assert find_context_row(document, "zagrtdenov_2025_table4_crystal_rims")["values"]["derivation_note"].find("controller, t-1382") >= 0
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 10
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["points"] == [] for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])


def test_isolated_migration_keeps_context_only_dataset_unscored():
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()
    assert migrator.result.observations == {}
