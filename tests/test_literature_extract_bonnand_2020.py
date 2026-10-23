from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate
from tests.helpers.literature_extract import find_context_row

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/bonnand-bruand-matzen-et-al-2020-redox-chromium-isotope-magnesiochromite.yaml"

# Independent transcriptions from the 600 dpi render of physical PDF page 40.
TABLE1_RENDER_GRID = [
    ("MgO", "11.78", "0.17", "11.74", "0.25"),
    ("Al2O3", "15.59", "0.15", "15.39", "0.29"),
    ("SiO2", "47.22", "0.31", "45.78", "0.43"),
    ("Cr2O3", "n.d.", "n.d.", "1.97", "0.04"),
    ("FeO", "7.36", "0.11", "7.41", "0.21"),
    ("CaO", "17.58", "0.16", "17.43", "0.45"),
    ("Total", "99.53", "0.38", "99.72", "0.51"),
]

# Independent transcription from the 600 dpi render of physical PDF page 41.
TABLE2_RENDER_GRID = [
    ("A1", "1300", "1", "-11.9", "-4.57", "Re", "168"),
    ("A2", "1300", "1", "-9.6", "-2.26", "Re", "168"),
    ("A3", "1300", "1", "-8.4", "-1.09", "Pt", "168"),
    ("A4", "1300", "1", "-7.5", "-0.13", "Pt", "168"),
    ("A5", "1300", "1", "-6.6", "0.74", "Pt", "168"),
    ("A6", "1300", "1", "-5.8", "1.58", "Pt", "168"),
    ("A7", "1300", "1", "-3.9", "3.40", "Pt", "168"),
    ("A3a", "1300", "1", "-8.4", "-1.09", "Pt", "2"),
    ("A3b", "1300", "1", "-8.4", "-1.09", "Pt", "24"),
    ("A3c", "1300", "1", "-8.4", "-1.09", "Pt", "72"),
    ("A3d", "1300", "1", "-8.4", "-1.09", "Pt", "120"),
    ("B1", "1300", "1", "-10.2", "-2.86", "Pt", "168"),
    ("B2", "1300", "1", "-7.8", "-0.46", "Pt", "168"),
    ("B3", "1300", "1", "-5.5", "1.84", "Pt", "168"),
]

# Each row contains all printed Table 3 cells in order: sample; six oxide
# means and their 2 s.d.; Total and 2 s.d.; NBO/T; both Fe ratios; both Cr ratios.
TABLE3_RENDER_GRID = [
    ("A1", "11.89", "0.15", "15.50", "0.22", "47.30", "0.24", "0.81", "0.04", "6.77", "0.16", "17.52", "0.16", "99.79", "0.41", "0.53", "0.01", "0.01", "7.64", "0.12"),
    ("A2", "11.76", "0.12", "15.41", "0.08", "46.99", "0.57", "0.28", "0.01", "7.44", "0.12", "17.61", "0.16", "99.49", "0.66", "0.55", "0.03", "0.03", "2.01", "0.33"),
    ("A3", "11.75", "0.14", "15.50", "0.21", "47.32", "0.22", "0.18", "0.01", "7.34", "0.15", "17.57", "0.13", "99.66", "0.52", "0.54", "0.06", "0.06", "0.95", "0.51"),
    ("A4", "11.79", "0.08", "15.24", "0.18", "47.34", "0.26", "0.15", "0.01", "7.42", "0.12", "17.49", "0.10", "99.42", "0.28", "0.55", "0.10", "0.09", "0.56", "0.64"),
    ("A5", "11.61", "0.09", "15.48", "0.11", "47.30", "0.35", "0.12", "0.01", "7.43", "0.09", "17.48", "0.15", "99.41", "0.50", "0.54", "0.17", "0.15", "0.26", "0.79"),
    ("A6", "11.44", "0.10", "15.36", "0.13", "46.93", "0.26", "0.09", "0.02", "7.66", "0.18", "17.62", "0.08", "99.10", "0.31", "0.55", "0.28", "0.22", "0.00", "1.00"),
    ("A7", "11.56", "0.07", "15.35", "0.13", "47.20", "0.21", "0.09", "0.02", "7.63", "0.12", "17.54", "0.18", "99.38", "0.18", "0.55", "0.80", "0.44", "-0.07", "1.07"),
    ("A3a", "11.79", "0.10", "15.63", "0.20", "47.20", "0.39", "0.02", "0.03", "7.34", "0.07", "17.64", "0.12", "99.62", "0.53", "0.54", "0.06", "0.06", "N/A", "N/A"),
    ("A3b", "11.80", "0.15", "15.59", "0.13", "47.23", "0.26", "0.12", "0.05", "7.37", "0.14", "17.58", "0.13", "99.67", "0.35", "0.54", "0.06", "0.06", "N/A", "N/A"),
    ("A3c", "11.79", "0.16", "15.61", "0.12", "47.20", "0.23", "0.16", "0.01", "7.42", "0.08", "17.55", "0.11", "99.72", "0.37", "0.54", "0.06", "0.06", "N/A", "N/A"),
    ("A3d", "11.76", "0.25", "15.54", "0.12", "47.24", "0.26", "0.18", "0.02", "7.38", "0.12", "17.52", "0.22", "99.62", "0.26", "0.54", "0.06", "0.06", "N/A", "N/A"),
    ("B1", "11.62", "0.21", "15.26", "0.17", "47.43", "0.36", "0.30", "0.06", "6.82", "0.18", "18.05", "0.33", "99.57", "0.64", "0.55", "0.02", "0.02", "2.25", "0.31"),
    ("B2", "11.56", "0.20", "15.21", "0.24", "47.17", "0.41", "0.15", "0.03", "7.38", "0.25", "17.98", "0.28", "99.59", "0.55", "0.56", "0.09", "0.08", "0.57", "0.64"),
    ("B3", "11.36", "0.34", "15.12", "0.46", "46.84", "0.52", "0.10", "0.03", "7.35", "0.21", "17.83", "0.75", "99.43", "1.67", "0.58", "0.32", "0.24", "0.07", "0.93"),
]
TABLE3_COMPONENTS = ("MgO", "Al2O3", "SiO2", "Cr2O3", "FeO", "CaO")
TABLE3_AUX_COLUMNS = ("Total_wt_pct", "Total_2sd_wt_pct", "NBO/T", "Cr2_over_Cr3", "Cr3_over_CrTOT")

# Independent transcription from the 600 dpi render of physical PDF page 43.
TABLE4_RENDER_GRID = [
    ("A1", "core", "12.11", "16.20", "0.51", "47.41", "23.32", "0.19", "99.8", "0.56", "0.66", "0.09"),
    ("A1", "rim", "17.50", "29.00", "0.02", "43.54", "9.89", "0.07", "100.2", "0.76", "0.50", "0.00"),
    ("A2", "core", "13.02", "16.10", "0.51", "47.98", "22.04", "0.14", "99.9", "0.60", "0.67", "0.09"),
    ("A2", "rim", "17.08", "27.75", "0.01", "43.97", "10.96", "0.04", "100.2", "0.75", "0.52", "0.01"),
    ("A3", "core", "10.85", "15.96", "0.50", "47.21", "25.00", "0.23", "99.8", "0.51", "0.67", "0.09"),
    ("A3", "rim", "15.50", "22.52", "0.22", "47.81", "12.23", "0.06", "98.9", "0.70", "0.59", "0.01"),
    ("A4", "core", "10.82", "15.82", "0.50", "47.59", "24.97", "0.23", "100.0", "0.51", "0.67", "0.09"),
    ("A4", "rim", "16.48", "24.87", "0.16", "45.40", "12.72", "0.06", "100.1", "0.73", "0.55", "0.02"),
    ("A5", "core", "12.42", "15.99", "0.50", "48.14", "22.85", "0.18", "100.1", "0.57", "0.67", "0.09"),
    ("A5", "rim", "15.80", "19.59", "0.40", "48.42", "15.65", "0.10", "100.2", "0.71", "0.62", "0.05"),
    ("A6", "core", "17.54", "16.05", "0.50", "49.20", "16.20", "0.08", "99.6", "0.78", "0.67", "0.10"),
    ("A6", "rim", "17.95", "20.79", "0.05", "38.98", "21.77", "0.04", "99.8", "0.78", "0.56", "0.17"),
    ("A7", "core", "18.22", "16.18", "0.52", "50.20", "15.00", "0.06", "100.2", "0.81", "0.68", "0.10"),
    ("A7", "rim", "18.66", "18.16", "0.06", "39.75", "23.17", "0.04", "100.2", "0.80", "0.60", "0.20"),
    ("B1", "core", "13.85", "8.02", "0.03", "64.91", "11.95", "0.00", "98.7", "0.67", "0.84", "0.00"),
    ("B1", "rim/small", "17.86", "27.01", "0.00", "44.48", "9.97", "0.06", "100.0", "0.78", "0.53", "0.01"),
    ("B2", "core", "14.73", "13.20", "0.01", "58.37", "12.30", "0.06", "98.7", "0.70", "0.75", "0.01"),
    ("B2", "rim/small", "17.92", "28.06", "0.00", "42.43", "11.75", "0.05", "100.7", "0.78", "0.50", "0.03"),
    ("B3", "core", "14.67", "8.36", "0.00", "62.37", "13.80", "0.06", "99.3", "0.71", "0.83", "0.04"),
    ("B3", "rim/small", "18.20", "25.46", "0.00", "39.12", "15.87", "0.05", "99.2", "0.80", "0.51", "0.10"),
]

# Independent transcription from the 600 dpi render of physical PDF page 44.
# The log fO2 value is resolved from that sample's single Table 2 condition.
TABLE5_RENDER_GRID = [
    ("A1", "-11.9", "-0.117", "0.006", "-0.110", "0.006"),
    ("A2", "-9.6", "-0.137", "0.005", "n.d.", "n.d."),
    ("A3", "-8.4", "-0.138", "0.005", "-0.115", "0.004"),
    ("A4", "-7.5", "-0.149", "0.005", "n.d.", "n.d."),
    ("A5", "-6.6", "-0.153", "0.004", "n.d.", "n.d."),
    ("A6", "-5.8", "-0.449", "0.005", "-0.102", "0.009"),
    ("A7", "-3.9", "-0.431", "0.005", "-0.108", "0.009"),
    ("A3a", "-8.4", "-0.251", "0.018", "n.d.", "n.d."),
    ("A3b", "-8.4", "-0.167", "0.007", "n.d.", "n.d."),
    ("A3c", "-8.4", "-0.147", "0.005", "n.d.", "n.d."),
    ("A3d", "-8.4", "-0.143", "0.005", "n.d.", "n.d."),
    ("B1", "-10.2", "-0.240", "0.006", "0.063", "0.010"),
    ("B2", "-7.8", "-0.274", "0.006", "0.088", "0.011"),
    ("B3", "-5.5", "-0.437", "0.008", "0.150", "0.008"),
    ("starting material", None, "-0.060", "0.007", None, None),
    ("JP-1", "N/A", "-0.108", "0.014*", None, None),
]


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _parent_observation_id(migrated_id, source_id, source_observation_ids):
    local_id = migrated_id.removeprefix(f"{source_id}::")
    matches = [
        observation_id
        for observation_id in source_observation_ids
        if local_id == observation_id or local_id.startswith(f"{observation_id}::")
    ]
    assert len(matches) == 1
    return matches[0]


def _table1_grid(document):
    rows = find_context_row(document, "bonnand_2020_table1_starting_melt_compositions")["values"]["rows_as_printed"]
    return [
        (row["oxide"], row["series_A_wt_pct"], row["series_A_2sd"], row["series_B_wt_pct"], row["series_B_2sd"])
        for row in rows
    ]


def _table2_grid(document):
    rows = {
        row["observation_id"].split("_")[2]: row
        for row in document["species"]["Cr"]["observations"]
        if row["values"].get("formula") == "MgO"
    }
    actual = []
    for sample in (row[0] for row in TABLE2_RENDER_GRID):
        observation = rows[sample]
        values = observation["values"]
        conditions = values["point_conditions"]
        pressure = conditions["total_pressure_Pa"]["state"]["value"]["point"]
        control = conditions["fO2_control"]["state"]["value"]
        log_fO2 = conditions["fO2_log"]["state"]["value"]
        duration = conditions["run_duration_h"]["state"]["value"]
        delta_qfm, loop = control.split("; oxygen loop=")
        actual.append(
            (sample, str(values["T_C"]), str(Decimal(pressure) / Decimal("100000")), str(log_fO2),
             delta_qfm.removeprefix("ΔQFM="), loop, str(duration))
        )
    return actual


def _table3_grid(document):
    component_rows = {}
    for observation in document["species"]["Cr"]["observations"]:
        if observation["locator"].get("table") != "3":
            continue
        values = observation["values"]
        sample = observation["locator"]["row"]
        formula = values["formula"]
        uncertainty = observation.get("uncertainty", {})
        component_rows[(sample, formula)] = (
            values["value_as_published"], uncertainty["sigma_as_published"]
        )
    iron_rows = {}
    for observation in document["species"]["Fe"]["observations"]:
        sample = observation["locator"]["row"]
        values = observation["values"]
        iron_rows.setdefault(sample, {})
        iron_rows[sample]["Fe3_over_Fe2"] = values["Fe3_over_Fe2"]
    auxiliary = {
        row["sample"]: row
        for row in find_context_row(document, "bonnand_2020_table3_auxiliary_columns")["values"]["rows_as_printed"]
    }
    actual = []
    for expected in TABLE3_RENDER_GRID:
        sample = expected[0]
        cells = [sample]
        for formula in TABLE3_COMPONENTS:
            cells.extend(component_rows[(sample, formula)])
        aux = auxiliary[sample]
        cells.extend(
            [
                aux["Total_wt_pct"],
                aux["Total_2sd_wt_pct"],
                aux["NBO/T"],
                iron_rows[sample]["Fe3_over_Fe2"],
                aux["Fe3_over_FeT"],
                aux["Cr2_over_Cr3"],
                aux["Cr3_over_CrTOT"],
            ]
        )
        actual.append(tuple(cells))
    return actual


def _table4_grid(document):
    row = find_context_row(document, "bonnand_2020_table4_chromite_compositions")
    columns = row["values"]["columns"]
    return [tuple(item[column] for column in columns) for item in row["values"]["rows_as_printed"]]


def _table5_grid(document):
    row = find_context_row(document, "bonnand_2020_table5_chromium_isotope_data")
    table2 = {line[0]: line[3] for line in _table2_grid(document)}
    actual = []
    for item in row["values"]["rows_as_printed"]:
        sample = item["sample"]
        condition_row = item["Table2_condition_row"]
        fO2 = None if condition_row is None else ("N/A" if condition_row == "N/A" else table2[condition_row])
        actual.append(
            (sample, fO2, item["melt_delta53Cr_permil"], item["melt_uncertainty_as_printed"],
             item["chromite_delta53Cr_permil"], item["chromite_uncertainty_as_printed"])
        )
    return actual


def test_render_transcriptions_bind_all_printed_tables_and_detect_mutations():
    document = _document()
    assert _table1_grid(document) == TABLE1_RENDER_GRID
    assert _table2_grid(document) == TABLE2_RENDER_GRID
    assert _table3_grid(document) == TABLE3_RENDER_GRID
    assert _table4_grid(document) == TABLE4_RENDER_GRID
    assert _table5_grid(document) == TABLE5_RENDER_GRID

    changed_grid = list(TABLE3_RENDER_GRID)
    changed_row = list(changed_grid[0])
    changed_row[1] = "11.88"
    changed_grid[0] = tuple(changed_row)
    with pytest.raises(AssertionError):
        assert _table3_grid(document) == changed_grid

    changed_extract = deepcopy(document)
    changed_cell = next(
        row
        for row in changed_extract["species"]["Cr"]["observations"]
        if row["observation_id"] == "bonnand_2020_A1_sio2"
    )
    changed_cell["values"]["value_as_published"] = "47.31"
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_extract) == TABLE3_RENDER_GRID


def test_isolated_migration_scores_all_typed_values_with_sample_conditions_and_species():
    document = _document()
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        row.observation_id: row for row in migrator.result.observations.values()
    }
    assert migrator.result.validation.hard_issues == ()
    assert len(observations) == 98
    assert all(row.value.kind.value == "point" for row in observations.values())
    assert all(not row.notices for row in observations.values())

    extracted = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }
    phase_rows = {
        _parent_observation_id(observation_id, document["source_id"], extracted): row
        for observation_id, row in observations.items()
        if row.identity.quantity.value.value == "equilibrium_phase_composition"
    }
    assert len(phase_rows) == 84
    for observation_id, migrated in phase_rows.items():
        source = extracted[observation_id]
        sample = source["locator"]["row"]
        values = source["values"]
        expected = Decimal(values["value_as_published"])
        assert values["component_amount"] == values["value_as_published"]
        assert migrated.value.point == expected
        assert migrated.identity.species.formula == values["formula"]
        assert migrated.identity.phase_role.is_value
        assert migrated.identity.phase_role.value.value == "glass"
        assert migrated.identity.species.phase.is_value
        assert migrated.identity.species.phase.value.value == "glass"
        assert values["quench_redox_preservation"] == "asserted_altered"
        assert values["quench_redox_preservation_locator"]["page"] == 4
        assert values["quench_redox_preservation_locator"]["pdf_page_index"] == 3
        assert "Cr2+ is oxidised" in values["quench_redox_preservation_locator"]["note"]
        assert values["quench_direction"] == "oxidising"
        quench_locator = values["quench_statement_locator"]
        assert quench_locator["page"] == 8 and quench_locator["pdf_page_index"] == 7
        assert "dropping them into water" in quench_locator["note"]
        assert migrated.identity.subtype.is_value
        assert migrated.identity.subtype.value == "wt_percent"
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.temperature_K.value == Decimal("1573.15")
        assert migrated.identity.total_pressure_Pa.is_value
        assert migrated.identity.total_pressure_Pa.value == Decimal("100000")
        assert migrated.identity.fO2_Pa.is_value
        assert migrated.identity.composition.is_value
        printed_sample = next(row for row in TABLE3_RENDER_GRID if row[0] == sample)
        expected_wt_percent = {
            formula: Decimal(printed_sample[1 + 2 * index])
            for index, formula in enumerate(TABLE3_COMPONENTS)
        }
        assert migrated.identity.composition.value.as_map() == (
            battery_migrate.wt_pct_to_mole_fraction(expected_wt_percent).as_map()
        )
        assert migrated.locator.page == 42 and migrated.locator.pdf_page_index == 41

        fO2 = migrated.point_conditions["fO2_log"]
        assert fO2.state.value == Decimal(next(row[3] for row in TABLE2_RENDER_GRID if row[0] == sample))
        assert fO2.locator.page == 41 and fO2.locator.pdf_page_index == 40
        assert migrated.point_conditions["gas_mix"].state.value == "CO-CO2"
        assert migrated.point_conditions["gas_mix"].locator.page == 7
        pressure = migrated.point_conditions["total_pressure_Pa"].state.value
        assert pressure.kind.value == "point" and pressure.point == Decimal("100000")

        uncertainty = source["uncertainty"]
        assert migrated.uncertainty.kind.value == "printed"
        assert Decimal(migrated.uncertainty.verbatim["sigma"]) == Decimal(uncertainty["sigma"])
        assert Decimal(uncertainty["sigma"]) == Decimal(uncertainty["sigma_as_published"])
        assert uncertainty["units"] == "wt.%"
        assert Decimal(migrated.uncertainty.verbatim["sigma"]) > 0

    iron_rows = {
        _parent_observation_id(observation_id, document["source_id"], extracted): row
        for observation_id, row in observations.items()
        if row.identity.quantity.value.value == "fe3_fe2_ratio"
    }
    assert len(iron_rows) == 14
    for observation_id, migrated in iron_rows.items():
        source = extracted[observation_id]
        values = source["values"]
        assert migrated.identity.species.formula == "Fe"
        assert migrated.identity.species.phase.is_value
        assert migrated.identity.species.phase.value.value == "l"
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.temperature_K.value == Decimal("1573.15")
        assert migrated.identity.total_pressure_Pa.is_value
        assert migrated.identity.total_pressure_Pa.value == Decimal("100000")
        assert migrated.identity.fO2_Pa.is_value
        assert migrated.identity.composition.is_value
        assert migrated.locator.page == 42 and migrated.locator.pdf_page_index == 41
        condition = migrated.point_conditions["fO2_log"]
        printed_conditions = next(row for row in TABLE2_RENDER_GRID if row[0] == source["locator"]["row"])
        expected_log_fO2 = Decimal(
            printed_conditions[3]
        )
        assert condition.state.value == expected_log_fO2
        assert condition.locator.page == 41
        assert migrated.point_conditions["gas_mix"].state.value == "CO-CO2"
        assert migrated.point_conditions["gas_mix"].locator.page == 7
        assert migrated.point_conditions["fO2_control"].state.value == (
            f"ΔQFM={printed_conditions[4]}; oxygen loop={printed_conditions[5]}"
        )
        assert migrated.point_conditions["run_duration_h"].state.value == int(printed_conditions[6])
        assert "quench_redox_preservation" not in values
        assert "quench_statement_locator" not in values
        assert migrated.value.kind.value == "point"
        assert "Fe3_over_Fe2" in values
        assert "Fe3_over_FeT" not in values
        assert migrated.value.point == Decimal(values["Fe3_over_Fe2"])

    context3 = find_context_row(document, "bonnand_2020_table3_auxiliary_columns")
    assert len(context3["values"]["rows_as_printed"]) == 14
    a7_context = next(row for row in context3["values"]["rows_as_printed"] if row["sample"] == "A7")
    assert a7_context["Fe3_over_FeT"] == "0.44"
    assert a7_context["Fe3_over_FeT_locator"] == {
        "page": 42, "pdf_page_index": 41, "published_page": 41,
        "table": "3", "row": "A7", "column": "Fe3+/FeTOT",
    }
    assert not any("Fe3_over_FeT" in row["values"] for row in document["species"]["Fe"]["observations"])
    a7_fe2 = iron_rows["bonnand_2020_A7_fe3_over_fe2"]
    assert a7_fe2.value.point == Decimal("0.80")
    context4 = find_context_row(document, "bonnand_2020_table4_chromite_compositions")
    assert "crystal/spinel role" in context4["values"]["schema_gap"]
    context5 = find_context_row(document, "bonnand_2020_table5_chromium_isotope_data")
    assert "identity.subtype" in context5["values"]["schema_gap"]


def test_figure_records_keep_axes_series_counts_and_defer_points():
    document = _document()
    figure_records = [
        row for row in document["species"]["Cr"]["context"] if row["type"] == "figure_record"
    ]
    assert len(figure_records) == 11
    for number, row in enumerate(figure_records, start=1):
        page = 28 + number
        assert row["locator"]["figure"] == str(number)
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["locator"]["published_page"] == page - 1
        assert row["values"]["digitisation"] == "deferred to a figure pass"
        assert row["values"]["points"] == []
        assert row["values"]["axes_as_printed"] is not None
        assert row["values"]["caption_as_printed"]
        assert all("marker_count" in series for series in row["values"]["series"])

    assert figure_records[6]["values"]["model_parameters_as_printed"] == {
        "panel_a_series_A_slope": "-0.27 ± 0.01",
        "panel_a_series_B_slope": "-0.25 ± 0.01",
    }
    assert figure_records[10]["values"]["model_parameters_as_printed"]["starting_Cr_concentration_ug_g"] == "13500"


def test_table3_a3_duplicate_is_one_observation_and_figure_and_context_locators_are_physical():
    document = _document()
    a3_rows = [
        row for row in document["species"]["Cr"]["observations"]
        if row["locator"].get("table") == "3" and row["locator"].get("row") == "A3"
    ]
    assert len(a3_rows) == len(TABLE3_COMPONENTS)
    assert all("represented once" in row["locator"]["note"] for row in a3_rows)
    for species in document["species"].values():
        for row in [*species["observations"], *species["context"]]:
            locator = row.get("locator", {})
            if "page" in locator and "pdf_page_index" in locator:
                assert locator["page"] == locator["pdf_page_index"] + 1
