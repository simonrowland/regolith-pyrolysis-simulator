from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate
from tests.helpers.literature_extract import find_context_row

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/zagrtdenov-2020-chromite-magnesiochromite-solubility.yaml"
FORMULAS = ("Na2O", "SiO2", "Al2O3", "K2O", "CaO", "MnO", "FeO", "MgO", "TiO2", "P2O5", "Cr2O3")

# Independent transcription from the 600 dpi render of physical PDF page 41.
TABLE3_GRID = [
    ("E23", "Chr-MORB", "FMQ-2", ("0.51(3)", "53.96(22)", "16.54(1)", "0.19(0.02)", "10.93(1)", "0.18(3)", "6.04(9)", "8.98(9)", "1.57(3)", "D.L.", "0.92(1)")),
    ("E19", "Chr-MORB", "FMQ", ("1.02(4)", "52.13(17)", "15.88(8)", "0.23(0.02)", "10.67(11)", "0.17(4)", "8.91(14)", "8.59(8)", "1.50(3)", "D.L.", "0.42(0)")),
    ("E27", "Chr-MORB", "FMQ+2", ("1.84(4)", "52.81(2)", "16.1(11)", "0.27(0.02)", "10.77(11)", "0.17(3)", "7.17(12)", "8.64(9)", "1.52(3)", "D.L.", "0.29(0)")),
    ("E24", "Chr-DiAn", "FMQ-2", ("D.L.", "49.77(2)", "9.21(7)", "D.L.", "24.66(2)", "D.L.", "1.04(4)", "15.03(11)", "D.L.", "0.07(2)", "0.88(1)")),
    ("E32", "Chr-DiAn", "FMQ", ("0.11(2)", "50.22(19)", "9.18(7)", "D.L.", "25.1(14)", "D.L.", "0.22(3)", "15.11(13)", "D.L.", "D.L.", "0.60(1)")),
    ("E28", "Chr-DiAn", "FMQ+2", ("0.20(2)", "50.2(19)", "9.12(8)", "D.L.", "25.01(15)", "D.L.", "0.51(4)", "14.94(11)", "D.L.", "0.07(2)", "0.48(0)")),
    ("E25", "Cr2O3-MORB", "FMQ-2", ("0.60(3)", "54.66(17)", "15.90(10)", "0.20(0.02)", "11.08(1)", "0.16(3)", "6.27(11)", "8.31(8)", "1.63(3)", "D.L.", "0.94(1)")),
    ("E31", "Cr2O3-MORB", "FMQ", ("1.26(4)", "52.90(13)", "15.41(1)", "0.25(0.02)", "10.78(11)", "0.17(3)", "8.54(11)", "8.00(8)", "1.58(3)", "D.L.", "0.45(1)")),
    ("E29", "Cr2O3-MORB", "FMQ+2", ("2.16(4)", "51.66(18)", "15.04(9)", "0.27(0.02)", "10.59(1)", "0.16(3)", "9.74(11)", "7.80(7)", "1.54(3)", "D.L.", "0.28(0)")),
    ("E26", "Cr2O3-DiAn", "FMQ-2", ("D.L.", "50.61(17)", "8.46(8)", "D.L.", "25.17(17)", "D.L.", "0.68(4)", "14.47(12)", "0.06(2)", "D.L.", "1.00(1)")),
    ("E33", "Cr2O3-DiAn", "FMQ", ("0.10(2)", "51.15(21)", "8.55(6)", "D.L.", "25.55(18)", "D.L.", "D.L.", "14.55(13)", "D.L.", "D.L.", "0.66(0)")),
    ("E30", "Cr2O3-DiAn", "FMQ+2", ("0.13(2)", "50.30(2)", "8.38(7)", "D.L.", "25.15(16)", "D.L.", "1.59(6)", "14.24(8)", "0.06(2)", "D.L.", "0.51(1)")),
]

TABLE1_GRID = [
    ("S2 chromite (31 analyses)", (None, None, "26.45(42)", None, None, "0.21(04)", "15.87(62)", "14.81(34)", "0.10(02)", None, "41.15(0.27)", "0.16(05)", "98.54", None)),
    ("Cr2O3 crystals (19 analyses)", (None, None, "0.14(1)", None, None, None, None, "0.28(28)", "1.45(3)", None, "97.92(1.19)", None, "99.79", None)),
    ("MORB 3786/3 (Emission-ICP)", ("3.04", "50.31", "15.31", "0.31", "10.29", "0.17", "8.91", "8.21", "1.45", "0.18", "0.04", None, "98.22", "275")),
    ("Di63An37 glass (15 analyses)", (None, "50.52(35)", "15.22(25)", None, "22.95(31)", None, None, "11.30(19)", None, None, None, None, "99.99", None)),
]
TABLE1_COLUMNS = ("Na2O", "SiO2", "Al2O3", "K2O", "CaO", "MnO", "FeO", "MgO", "TiO2", "P2O5", "Cr2O3", "NiO", "Total", "Cr_ppm")

# Independent Table 2 transcription from the 600 dpi render of physical PDF page 40.
TABLE2_GRID = [
    ("E23", "rim", 5, "Chr-MORB", "FMQ-2", "Chr", ("14.71(1.09)", "D.L.", "0.29(4)", "12.04(19)", "14.47(16)", "0.39(2)", "55.42(1.03)", "0.32(3)"), "97.66", "1.78"),
    ("E19", "rim", 5, "Chr-MORB", "FMQ", "Chr", ("16.24(1.68)", "0.09(5)", "0.24(4)", "16.82(12)", "13.07(33)", "0.41(1)", "51.53(2.17)", "0.15(2)"), "98.55", "2.09"),
    ("E27", "rim", 14, "Chr-MORB", "FMQ+2", "Chr", ("16.35(3.21)", "D.L.", "0.27(3)", "16.49(33)", "15.05(91)", "0.36(4)", "49.67(4.25)", "D.L."), "98.18", "2.23"),
    ("E24", "rim", 5, "Chr-DiAn", "FMQ-2", "Chr", ("11.45(1.65)", "D.L.", "D.L.", "1.73(7)", "20.6(21)", "D.L.", "63.78(1.84)", "D.L."), "97.56", "1.86"),
    ("E28", "rim", 10, "Chr-DiAn", "FMQ+2", "Chr", ("10.26(1.57)", "D.L.", "D.L.", "1.06(5)", "21.26(23)", "D.L.", "65.43(2.08)", "D.L."), "98.01", "2.14"),
    ("E25", "center", 2, "Cr2O3-MORB", "FMQ-2", "Chr", ("9.37(1.97)", "D.L.", "0.27(5)", "13.27(25)", "13.06(32)", "0.36(0)", "61.22(1.98)", "0.19(1)"), "97.75", "1.81"),
    ("E25", "rim", 5, "Cr2O3-MORB", "FMQ-2", "Chr", ("11.65(0.49)", "D.L.", "0.30(03)", "13.25(12)", "13.47(23)", "0.41(1)", "58.67(0.51)", "0.27(2)"), "98.04", "1.80"),
    ("E31", "center", 12, "Cr2O3-MORB", "FMQ", "Cr2O3", ("D.L.", "D.L.", "D.L.", "0.64(28)", "0.18(16)", "1.53(3)", "97.48(0.9)", "0.11(1)"), "99.94", "2.34"),
    ("E31", "rim", 17, "Cr2O3-MORB", "FMQ", "Chr", ("4.15(3.26)", "D.L.", "0.28(4)", "16.52(38)", "10.47(1.14)", "0.45(5)", "66.64(4.59)", "0.1(3)"), "98.61", "2.17"),
    ("E29", "center", 5, "Cr2O3-MORB", "FMQ+2", "Cr2O3", ("0.22(3)", "0.14(11)", "D.L.", "1.08(75)", "0.30(27)", "1.55(58)", "95.32(2.69)", "D.L."), "98.60", "2.52"),
    ("E29", "rim", 15, "Cr2O3-MORB", "FMQ+2", "Chr", ("5.1(3.07)", "D.L.", "0.28(5)", "18.56(2.48)", "10.92(1.37)", "0.34(5)", "63.22(7.14)", "D.L."), "98.42", "2.35"),
    ("E26", "center", 3, "Cr2O3-DiAn", "FMQ-2", "MgChr", ("4.77(7)", "D.L.", "D.L.", "1.28(4)", "20.14(7)", "D.L.", "71.78(7)", "D.L."), "97.97", "1.86"),
    ("E26", "rim", 5, "Cr2O3-DiAn", "FMQ-2", "MgChr", ("7.72(72)", "D.L.", "D.L.", "1.25(3)", "20.48(2)", "D.L.", "68.16(92)", "D.L."), "97.61", "1.83"),
    ("E33", "center", 19, "Cr2O3-DiAn", "FMQ", "Cr2O3", ("0.14(1)", "D.L.", "D.L.", "D.L.", "0.28(28)", "1.45(3)", "97.92(1.19)", "D.L."), "99.79", "2.17"),
    ("E33", "rim", 12, "Cr2O3-DiAn", "FMQ", "MgChr", ("4.29(2.68)", "D.L.", "D.L.", "D.L.", "20.68(46)", "0.14(11)", "73.32(3.93)", "D.L."), "98.43", "2.05"),
    ("E30", "center", 14, "Cr2O3-DiAn", "FMQ+2", "Cr2O3", ("0.13(13)", "D.L.", "D.L.", "0.21(6)", "0.31(34)", "1.11(53)", "98.13(1.39)", "0.12(1)"), "100.00", "2.28"),
    ("E30", "rim", 15, "Cr2O3-DiAn", "FMQ+2", "MgChr", ("3.11(2.05)", "D.L.", "D.L.", "2.72(33)", "19.31(48)", "0.15(11)", "73.35(3.5)", "D.L."), "98.64", "2.16"),
]
TABLE2_COMPONENTS = ("Al2O3", "SiO2", "MnO", "FeO", "MgO", "TiO2", "Cr2O3", "V2O3")


def _table1_grid(document):
    row = find_context_row(document, "zagrtdenov_2020_table1_starting_material_compositions")
    return [
        (entry["starting_material"], tuple(entry["cells_as_printed"][column] for column in TABLE1_COLUMNS))
        for entry in row["values"]["rows_as_printed"]
    ]


def _stored_cell(raw):
    if raw == "D.L.":
        return raw
    mean, separator, sd = raw.partition("(")
    if not separator:
        return raw
    sd = sd.removesuffix(")")
    # Printed zero-width standard deviations are intentionally omitted by the family rule.
    return mean if Decimal(sd) == 0 else raw


def _table2_grid(document):
    rows = find_context_row(document, "zagrtdenov_2020_table2_crystal_compositions_and_log_kd")["values"]["rows_as_printed"]
    actual = []
    for row in rows:
        components = []
        for formula in TABLE2_COMPONENTS:
            cell = row["composition_wt_percent_as_printed"][formula]
            if cell.get("printed") == "D.L.":
                components.append("D.L.")
            else:
                mean = cell["mean_wt_percent_as_printed"]
                sd = cell.get("standard_deviation_wt_percent_as_printed")
                components.append(mean if sd is None else f"{mean}({sd})")
        actual.append((row["run"], row["rim_or_center"], row["number_of_analyses"], row["system"], row["buffer"], row["phase"], tuple(components), row["total_wt_percent_as_printed"], row["log10_chromium_distribution_coefficient_as_printed"]))
    return actual


def _table3_grid(document):
    rows = {}
    for observation in document["species"]["Cr"]["observations"]:
        values = observation["values"]
        if observation["locator"].get("table") != "3":
            continue
        sample = observation["locator"]["row"]
        cell = values["value_as_published"]
        if cell != "D.L.":
            uncertainty = observation.get("uncertainty", {})
            sd = uncertainty.get("sigma_as_published")
            if sd is not None:
                places = len(cell.partition(".")[2])
                cell = f"{cell}({Decimal(sd):.{places}f})"
        rows.setdefault(sample, {})[values["formula"]] = cell
    metadata = {
        row["sample"]: (row["system_as_printed"], row["buffer_as_printed"])
        for row in find_context_row(document, "zagrtdenov_2020_table3_noncomponent_columns")["values"]["rows_as_printed"]
    }
    return [
        (sample, *metadata[sample], tuple(rows[sample][formula] for formula in FORMULAS))
        for sample in rows
    ]


def _stored_table2_grid():
    return [(*row[:6], tuple(_stored_cell(cell) for cell in row[6]), *row[7:]) for row in TABLE2_GRID]


def _stored_table3_grid():
    return [
        (sample, system, buffer, tuple(_expanded_table3_cell(cell) for cell in cells))
        for sample, system, buffer, cells in TABLE3_GRID
    ]


def _expanded_table3_cell(cell):
    stored = _stored_cell(cell)
    if stored == "D.L." or "(" not in stored:
        return stored
    mean, raw_sigma = stored.removesuffix(")").split("(")
    places = len(mean.partition(".")[2])
    sigma = Decimal(raw_sigma) if "." in raw_sigma else Decimal(raw_sigma).scaleb(-places)
    return f"{mean}({sigma:.{places}f})"


def test_render_transcriptions_bind_every_table_and_detect_mutations():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _table1_grid(document) == TABLE1_GRID
    assert _table2_grid(document) == _stored_table2_grid()
    assert _table3_grid(document) == _stored_table3_grid()

    changed_expected_table3 = list(_stored_table3_grid())
    row = list(changed_expected_table3[0])
    changed_cells = list(row[3])
    changed_cells[1] = "53.69(22)"
    changed_expected_table3[0] = (*row[:3], tuple(changed_cells))
    with pytest.raises(AssertionError):
        assert _table3_grid(document) == changed_expected_table3

    changed_extract = deepcopy(document)
    changed_cell = next(
        observation
        for observation in changed_extract["species"]["Cr"]["observations"]
        if observation["observation_id"] == "zagrtdenov_2020_E23_sio2"
    )
    changed_cell["values"]["value_as_published"] = "53.69"
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_extract) == _stored_table3_grid()

    changed_expected_table2 = list(_stored_table2_grid())
    row2 = list(changed_expected_table2[0])
    changed_components = list(row2[6])
    changed_components[0] = "14.17(1.09)"
    changed_expected_table2[0] = (*row2[:6], tuple(changed_components), *row2[7:])
    with pytest.raises(AssertionError):
        assert _table2_grid(document) == changed_expected_table2

    changed_table2_extract = deepcopy(document)
    table2_row = find_context_row(changed_table2_extract, "zagrtdenov_2020_table2_crystal_compositions_and_log_kd")["values"]["rows_as_printed"][0]
    table2_row["composition_wt_percent_as_printed"]["Al2O3"]["mean_wt_percent_as_printed"] = "14.17"
    with pytest.raises(AssertionError):
        assert _table2_grid(changed_table2_extract) == _stored_table2_grid()


def test_isolated_migration_scores_numeric_glass_components_and_preserves_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(observations) == 132
    assert migrator.result.validation.hard_issues == ()
    assert len([row for row in observations.values() if row.value.kind.value == "point"]) == 103
    assert len([row for row in observations.values() if row.value.kind.value == "unavailable"]) == 29
    assert {
        sample["observation_id"] for sample in document["fidelity_samples"]
    } <= observations.keys()

    expected = {
        (sample, formula): cell
        for sample, _system, _buffer, cells in TABLE3_GRID
        for formula, cell in zip(FORMULAS, cells, strict=True)
    }
    experiment_ids = {}
    for observation_id, row in observations.items():
        yaml_row = next(
            observation
            for observation in document["species"]["Cr"]["observations"]
            if observation["observation_id"] == observation_id
        )
        sample = yaml_row["locator"]["row"]
        formula = yaml_row["values"]["formula"]
        cell = expected[(sample, formula)]
        values = yaml_row["values"]
        assert row.identity.quantity.value.value == "equilibrium_phase_composition"
        assert row.identity.species.formula == formula
        assert row.identity.phase_role.is_value and row.identity.phase_role.value.value == "glass"
        assert row.identity.species.phase.is_value and row.identity.species.phase.value.value == "glass"
        assert row.identity.subtype.is_value and row.identity.subtype.value == "wt_percent"
        assert row.identity.temperature_K.is_value and row.identity.temperature_K.value == Decimal("1713.15")
        assert row.locator.page == 41 and row.locator.pdf_page_index == 40
        assert row.experiment_id is not None
        experiment_ids.setdefault(sample, set()).add(row.experiment_id)
        assert row.point_conditions["gas_mix"].state.value == "CO-CO2"
        assert row.point_conditions["gas_mix"].locator.page == 6
        assert row.point_conditions["fO2_control"].state.value == next(buffer for row_sample, _system, buffer, _cells in TABLE3_GRID if row_sample == sample)
        assert row.point_conditions["fO2_control"].locator.page == 41
        assert row.point_conditions["total_pressure_Pa"].locator.page == 6
        assert "fO2_log" not in row.point_conditions
        assert not row.notices
        if cell == "D.L.":
            assert row.value.kind.value == "unavailable"
            assert values["component_amount"]["tag"] == "unknown"
            assert values["component_amount"]["reason"] == "below_detection_limit"
            assert values["value_as_published"] == "D.L."
        else:
            mean = cell.partition("(")[0]
            assert row.value.kind.value == "point"
            assert row.value.point == Decimal(mean)
            assert Decimal(values["component_amount"]) == Decimal(mean)
            uncertainty = yaml_row.get("uncertainty")
            if "(" in cell and Decimal(cell.partition("(")[2].removesuffix(")")) != 0:
                raw_sigma = cell.partition("(")[2].removesuffix(")")
                places = len(mean.partition(".")[2])
                expected_sigma = Decimal(raw_sigma) if "." in raw_sigma else Decimal(raw_sigma).scaleb(-places)
                assert Decimal(uncertainty["sigma_as_published"]) == expected_sigma
                assert uncertainty["sigma_units_as_published"] == "wt.%"
                assert uncertainty["units"] == "wt.%"
                assert Decimal(uncertainty["sigma"]) == expected_sigma
                assert row.uncertainty.kind.value == "printed"
                assert row.uncertainty.verbatim["sigma"] == uncertainty["sigma"]
            else:
                assert uncertainty is None
    assert len(experiment_ids) == 12
    assert all(len(ids) == 1 for ids in experiment_ids.values())
    assert len({next(iter(ids)) for ids in experiment_ids.values()}) == 12
    assert observations["zagrtdenov_2020_E23_na2o"].uncertainty.verbatim["sigma"] == "0.03"
    assert observations["zagrtdenov_2020_E23_k2o"].uncertainty.verbatim["sigma"] == "0.02"
    assert observations["zagrtdenov_2020_E23_cr2o3"].uncertainty.verbatim["sigma"] == "0.01"

    all_rows = [*document["species"]["Cr"]["observations"], *document["species"]["Cr"]["context"]]
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in all_rows)
    figure_records = [row for row in document["species"]["Cr"]["context"] if row["type"] == "figure_record"]
    assert len(figure_records) == 14
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_records)
    assert all(row["values"]["points"] == [] for row in figure_records)
    assert all("marker_count" in series for row in figure_records for series in row["values"]["series"])


def test_context_preserves_starting_crystal_tables_auxiliary_columns_and_model_fit():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = find_context_row(document, "zagrtdenov_2020_table1_starting_material_compositions")
    assert len(table1["values"]["rows_as_printed"]) == 4
    assert table1["values"]["rows_as_printed"][2]["cells_as_printed"]["Cr_ppm"] == "275"

    table2 = find_context_row(document, "zagrtdenov_2020_table2_crystal_compositions_and_log_kd")
    assert len(table2["values"]["rows_as_printed"]) == 17
    assert table2["values"]["rows_as_printed"][0]["log10_chromium_distribution_coefficient_as_printed"] == "1.78"
    assert table2["values"]["rows_as_printed"][-1]["composition_wt_percent_as_printed"]["Cr2O3"]["mean_wt_percent_as_printed"] == "73.35"

    table3_aux = find_context_row(document, "zagrtdenov_2020_table3_noncomponent_columns")
    assert len(table3_aux["values"]["rows_as_printed"]) == 12
    assert table3_aux["values"]["rows_as_printed"][9]["Cr_ppm_as_printed"] == "6824(56)"
    model = find_context_row(document, "zagrtdenov_2020_model_eq19b_fit")
    assert model["values"]["coefficient_values_with_uncertainty_as_printed"]["a"] == "-7.01 ± 2.10"
    assert model["values"]["fit_comparison_as_printed"]["R_squared"] == "0.94"


def test_source_test_needs_no_external_corpus_files():
    # This test reads only the committed extract; REGOLITH_CORPUS_ROOT may point at an absent tree.
    assert EXTRACT.is_file()
