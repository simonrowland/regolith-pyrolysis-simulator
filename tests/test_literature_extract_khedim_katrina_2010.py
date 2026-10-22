from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/khedim-katrina-podor-panteix-rapin-vilasi-2010-cr-soda-lime-silicate.yaml"

_TABLE2_EXPECTED = [
    ("Na2O-SiO2", "Na2O-1.5SiO2", "-7.66", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-SiO2", "Na2O-2SiO2", "-8.24", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-SiO2", "Na2O-2.5SiO2", "-8.77", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-SiO2", "Na2O-3SiO2", "-9.35", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-SiO2", "Na2O-3.5SiO2", "-9.62", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-SiO2", "Na2O-4SiO2", "-9.43", 'Neudorf and Elliott (1980), reference 50; "Thermodynamic Properties of Na2O-SiO2-CaO Melts at 1000°C to 1100°C", Metallurgical Transactions B-Process Metallurgy, 11[4] 607-614.'),
    ("Na2O-CaO-SiO2", "Na2O-CaO-3SiO2", "-8.78", 'Abdelouhab et al. (2007), reference 46; "Determination of Na2O activities in silicate melts by EMF measurements", Journal of Non-Crystalline Solids.'),
    ("Na2O-CaO-SiO2", "Na2O-CaO-4SiO2", "-9.69", 'Abdelouhab et al. (2007), reference 46; "Determination of Na2O activities in silicate melts by EMF measurements", Journal of Non-Crystalline Solids.'),
    ("Na2O-CaO-SiO2", "Na2O-CaO-7SiO2", "-10.28", 'Abdelouhab et al. (2007), reference 46; "Determination of Na2O activities in silicate melts by EMF measurements", Journal of Non-Crystalline Solids.'),
]

_TABLE1_EXPECTED = [
    ("Na2O-1.5SiO2", "69.68", "59.25", "—", "25.95", "0.40", "20.17", "0.19", "—", None, "1.56", "0.04"),
    ("Na2O-2SiO2", "58.19", "65.97", "—", "21.91", "0.44", "22.38", "0.22", "—", None, "2.04", "0.06"),
    ("Na2O-2.5SiO2", "49.95", "70.79", "—", "19.22", "0.49", "23.72", "0.25", "—", None, "2.47", "0.09"),
    ("Na2O-3SiO2", "43.75", "74.41", "—", "16.34", "0.22", "25.16", "0.11", "—", None, "3.08", "0.06"),
    ("Na2O-3.5SiO2", "38.93", "77.24", "—", "15.00", "0.33", "25.83", "0.17", "—", None, "3.466", "0.10"),
    ("Na2O-CaO-3SiO2", "40.89", "60.42", "35.21", "14.04", "0.25", "21.32", "0.15", "7.02", "0.09", "3.03", "0.06"),
    ("Na2O-CaO-4SiO2", "34.04", "67.06", "29.31", "10.98", "0.16", "23.64", "0.12", "5.85", "0.10", "4.30", "0.08"),
    ("Na2O-CaO-7SiO2", "22.64", "78.08", "19.50", "7.57", "0.27", "26.82", "0.14", "3.70", "0.08", "7.09", "0.17"),
]


def _load_extract():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table2_grid(document):
    record = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "khedim_katrina_2010_table2_quoted_log_na2o_activities"
    )
    values = record["values"]
    return [
        (series["system"], row["composition"], row["log10_activity_as_printed"], series["attribution"])
        for series in values["series"]
        for row in series["rows"]
    ]


def _table1_grid(document):
    record = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "khedim_katrina_2010_table1_starting_glass_compositions"
    )
    return [
        (
            row["aimed_glass"], row["Na2CO3_wt_percent"], row["SiO2_wt_percent"],
            row["CaCO3_wt_percent"], row["EPMA_Na_at_percent"], row["EPMA_Na_parenthetical"],
            row["EPMA_Si_at_percent"], row["EPMA_Si_parenthetical"], row["EPMA_Ca_at_percent"],
            row.get("EPMA_Ca_parenthetical"), row["x"], row["x_parenthetical"],
        )
        for row in record["values"]["compositions_as_printed"]
    ]


def _context(document, observation_id):
    return next(
        row
        for species in document["species"].values()
        for row in species["context"]
        if row["observation_id"] == observation_id
    )


def test_table2_grid_matches_independent_600dpi_transcription():
    """Pin the nine printed cells, composition labels, and cited attribution."""
    document = _load_extract()
    assert _table2_grid(document) == _TABLE2_EXPECTED
    table2 = _context(document, "khedim_katrina_2010_table2_quoted_log_na2o_activities")
    assert table2["T_C"] == 1250
    assert table2["values"]["method_class"] == "quoted_attributed"
    assert "printed_form field" in table2["values"]["schema_gap"]


def test_table2_cell_or_independent_grid_mutation_is_rejected():
    """The render transcription and extract each independently guard a table cell."""
    document = _load_extract()
    mutated_document = deepcopy(document)
    _context(mutated_document, "khedim_katrina_2010_table2_quoted_log_na2o_activities")["values"]["series"][0]["rows"][0][
        "log10_activity_as_printed"
    ] = "-7.65"
    with pytest.raises(AssertionError):
        assert _table2_grid(mutated_document) == _TABLE2_EXPECTED

    mutated_grid = list(_TABLE2_EXPECTED)
    mutated_grid[0] = (*mutated_grid[0][:2], "-7.65", mutated_grid[0][3])
    with pytest.raises(AssertionError):
        assert _table2_grid(document) == mutated_grid


def test_tables_1_3_4_and_5_match_independent_600dpi_transcriptions():
    document = _load_extract()
    assert _table1_grid(document) == _TABLE1_EXPECTED

    table3 = _context(document, "khedim_katrina_2010_table3_calculated_optical_basicity")
    basicity = table3["values"]["values_as_printed"]
    assert [(row["x"], row["optical_basicity"]) for row in basicity["binary_Na2O_xSiO2"]] == [
        ("1.5", "0.748"), ("2", "0.703"), ("2.5", "0.663"), ("3", "0.648")
    ]
    assert [(row["x"], row["optical_basicity"]) for row in basicity["ternary_Na2O_CaO_xSiO2"]] == [
        ("3", "0.718"), ("4", "0.678"), ("7", "0.612")
    ]

    table4 = _context(document, "khedim_katrina_2010_table4_interpolated_log_activities")
    assert [
        (
            row["ternary_composition"], row["log_a_Na2O"], row["equivalent_binary_Na2O_SiO2"],
            row["log_a_O2_minus"], row["equivalent_binary_for_O2_minus"],
        )
        for row in table4["values"]["values_as_printed"]
    ] == [
        ("Na2O-CaO-3SiO2", "-8.75", "Na2O-2.5SiO2", "-7.97", "Na2O-1.7SiO2"),
        ("Na2O-CaO-4SiO2", "-9,66", "Na2O-3.3SiO2", "-8.60", "Na2O-2.2SiO2"),
        ("Na2O-CaO-7SiO2", "-10,42", "—", "-10.24", "Na2O-4SiO2"),
    ]

    table5 = _context(document, "khedim_katrina_2010_table5_reaction_thermodynamics")
    values = table5["values"]["values_as_printed"]
    assert [
        (row["delta_H_kJ_mol"], row["delta_H_uncertainty"], row["delta_S_J_mol_K"], row["delta_S_uncertainty"])
        for row in values.values()
    ] == [("33", "11", "26", "9"), ("50", "15", "54", "16"), ("172", "37", "61", "16")]
    assert "printed equation writes Cr(VI)O_y" in table5["values"]["equation_9_note"]


def test_context_tables_and_figures_survive_isolated_migration_without_scored_points():
    """Context-only values stay unscored until the base can preserve printed log form."""
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.observations == {}
    assert result.validation.hard_issues == ()

    document = _load_extract()
    assert len(document["species"]["Na2O"]["context"]) == 4
    chromium_context = document["species"]["Cr"]["context"]
    figure_records = [
        row
        for species in document["species"].values()
        for row in species["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figure_records) == 10
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in figure_records
    )
    table5 = chromium_context[0]
    assert table5["values"]["values_as_printed"]["dissolution"]["delta_H_kJ_mol"] == "33"
    assert table5["values"]["values_as_printed"]["reduction"]["delta_S_J_mol_K"] == "61"
