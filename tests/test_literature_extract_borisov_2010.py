from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-2010-silica-ferric-ferrous.yaml"
MAPPING = ROOT / "data/literature/extracts/audits/borisov-2010-silica-ferric-ferrous.mapping.yaml"
SOURCE_ID = "borisov-2010-silica-ferric-ferrous"


TABLE4_RUNS = {
    "DAFL-9": ("1424", "3.44", "DAFL"),
    "DAFL-10": ("1540", "2.45", "DAFL"),
    "DAF-1": ("1500", "2.08", "DAF"),
    "DAS20F-1": ("1500", "1.82", "DAS20F"),
    "DAS40F-1": ("1500", "1.65", "DAS40F"),
    "DAS60F-1": ("1500", "1.73", "DAS60F"),
    "DAS80F-1": ("1500", "1.78", "DAS80F"),
    "DAF-2": ("1398", "2.94", "DAF"),
    "DAS20F-2": ("1398", "2.56", "DAS20F"),
    "DAS40F-2": ("1398", "2.29", "DAS40F"),
    "DAS60F-2": ("1398", "2.68", "DAS60F"),
    "DAF-5": ("1571", "1.58", "DAF"),
    "DAS20F-5": ("1571", "1.44", "DAS20F"),
    "DAS40F-5": ("1571", "1.41", "DAS40F"),
    "DAS60F-5": ("1571", "1.36", "DAS60F"),
    "DAS80F-5": ("1571", "1.38", "DAS80F"),
    "DAS90F-5": ("1571", "1.53", "DAS90F"),
    "DAF-6": ("1450", "2.39", "DAF"),
    "DAS20F-6": ("1450", "2.11", "DAS20F"),
    "DAS40F-6": ("1450", "2.01", "DAS40F"),
    "DAS60F-6": ("1450", "2.00", "DAS60F"),
    "DAS80F-6": ("1450", "2.15", "DAS80F"),
}
# Folio 546, Table 1 columns: SiO2, Al2O3, Fe2O3, MgO, CaO, Total, x.
TABLE1_MIXTURES = {
    "DAF": ("47.9", "15.1", "5.2", "9.7", "22.1", "100.0", "0"),
    "DAS20F": ("53.2", "13.4", "5.1", "8.7", "19.6", "100.0", "11"),
    "DAS40F": ("58.5", "11.7", "5.2", "7.5", "17.1", "100.0", "21"),
    "DAS60F": ("63.7", "10", "5.1", "6.5", "14.7", "100.0", "32"),
    "DAS80F": ("69.1", "8.3", "5.1", "5.3", "12.1", "100.0", "43"),
    "DAS90F": ("74.7", "6.5", "5.0", "4.2", "9.6", "100.0", "54"),
    "DAFL": ("50.3", "15.6", "1.1", "10.1", "22.9", "100.0", "–"),
}
OXIDES = ("SiO2", "Al2O3", "Fe2O3", "MgO", "CaO")

# Folio 546, Table 2 groups samples in the same printed cell.
TABLE2_RUNS = {
    "1398": ("88", ("DA-2", "DAS20F-2", "DAS40F-2", "DAS60F-2")),
    "1424": ("42", ("DAFL-9",)),
    "1450": ("70", ("DA-6", "DAS20F-6", "DAS40F-6", "DAS60F-6", "DAS80F-6")),
    "1500": ("20", ("DA-1", "DAS20F-1", "DAS40F-1", "DAS60F-1", "DAS80F-1")),
    "1540": ("21", ("DAFL-10",)),
    "1571": ("23", ("DA-5", "DAS20F-5", "DAS40F-5", "DAS60F-5", "DAS80F-5", "DAS90F-5")),
}
TABLE4_VELOCITIES = {"DAFL-9": "19", "DAFL-10": "19"}


def _cell(folio, index, table, row, column):
    return dict(published_page=folio, pdf_page_index=index, table=table, record=row, column=column)


def _check_run_cells(row):
    run = row["run_id_as_printed"]
    temperature, ratio, series = TABLE4_RUNS[run]
    duration, samples = TABLE2_RUNS[temperature]
    sample = run.replace("DAF-", "DA-", 1) if series == "DAF" else run
    assert sample in samples
    ratio_cell = _cell(549, 4, "4", run, "Fe3+/Fe2+")
    pressure = dict(
        published_page=546, pdf_page_index=1, section="Experimental Methods",
        record="one atmosphere vertical tube furnace",
    )
    cells = {
        "run_id_as_printed": _cell(548, 3, "4", run, "Sample no."),
        "Fe3_over_Fe2": ratio_cell,
        "value": ratio_cell,
        "T_C": _cell(546, 1, "2", temperature, "T °C"),
        "duration_h_as_printed": _cell(546, 1, "2", temperature, "h"),
        "temperature_run_id_as_printed": _cell(546, 1, "2", temperature, "samples"),
        "atmosphere_as_printed": _cell(546, 1, "2", "note", "atmosphere"),
        "total_pressure_atm": pressure,
        "sample_series_as_printed": _cell(546, 1, "1", series, "Sample"),
        "composition_as_printed": _cell(546, 1, "1", series, "Sample"),
        "composition_total_wt_pct_as_printed": _cell(546, 1, "1", series, "Total"),
        "silica_formula_x_as_printed": _cell(546, 1, "1", series, "x"),
        **{f"composition.{oxide}": _cell(546, 1, "1", series, oxide) for oxide in OXIDES},
    }
    if run in TABLE4_VELOCITIES:
        cells["vmax_mm_s_as_printed"] = _cell(548, 3, "4", run, "vmax (mm/s)")
    for field, locator in cells.items():
        assert row["cell_locators"].get(field) == locator, (run, field)
    assert row["cell_locators"].keys() == cells.keys()
    assert row.get("vmax_mm_s_as_printed") == TABLE4_VELOCITIES.get(run)
    assert row["locator"] == ratio_cell
    assert row["value"] == row["Fe3_over_Fe2"] == ratio
    assert row["temperature_run_id_as_printed"] == sample
    assert row["T_C"] == temperature
    assert row["duration_h_as_printed"] == duration
    assert row["atmosphere_as_printed"] == "air"
    assert row["total_pressure_atm"] == 1
    assert row["sample_series_as_printed"] == row["composition_as_printed"] == series
    mixture = TABLE1_MIXTURES[series]
    assert row["composition_total_wt_pct_as_printed"] == mixture[-2]
    assert row["silica_formula_x_as_printed"] == mixture[-1]

    conditions = row["point_conditions"]
    for condition, field in (
        ("T_C", "T_C"),
        ("duration_h", "duration_h_as_printed"),
        ("atmosphere_as_printed", "atmosphere_as_printed"),
        ("total_pressure_atm", "total_pressure_atm"),
    ):
        assert conditions[condition]["locator"] == cells[field]
    assert conditions["total_pressure_atm"]["units"] == "atm"
    assert conditions["total_pressure_atm"]["as_printed"] == "one atmosphere"
    composition_cell = _cell(546, 1, "1", series, "initial-mixture oxides")
    assert conditions["composition"]["locator"] == row["composition_locator"] == composition_cell
    assert conditions["composition"]["state"]["value"] == {
        "amount_basis": "mass_percent",
        "components": dict(zip(OXIDES, mixture[:5], strict=True)),
    }


def test_borisov_2010_table4_runs_keep_printed_values_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    series = document["species"]["Fe"]["observations"][0]
    values = series["values"]
    source_rows = values["rows"]
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    assert mapping["source_id"] == SOURCE_ID
    table4 = next(table for table in mapping["tables"] if table["table"] == "4")
    assert table4["printed_data_rows"] == table4["extracted_run_rows"] == 22
    assert len(mapping["render_check"]["verified_cells"]) >= 15
    table3 = next(table for table in mapping["tables"] if table["table"] == "3")
    comparison_runs = document["experiments"][0]["comparison_runs"]
    comparison_fields = (
        "sample_id_as_printed", "T_C", "source_relation",
        "fO2_as_printed", "log_fO2", "log_fO2_basis",
    )
    assert table3["mapped_fields"] == [
        f"experiments[0].comparison_runs[].{field}" for field in comparison_fields
    ]
    for run, name in zip(comparison_runs, ("DAFe-11", "DAS25Fe-11", "DAS70Fe-11"), strict=True):
        assert tuple(run[field] for field in comparison_fields) == (
            name, "1400", "Borisov (2007) reduced-glass experiment",
            "10⁻¹²·³ atm", "-12.3", "log10(fO2 / 1 atm)",
        )
        assert run["sample_locator"] == _cell(547, 2, "3", name, "Sample no.")
        assert run["fO2_locator"] == dict(
            published_page=546, pdf_page_index=1, section="Experimental Methods",
        )
        assert run["log_fO2_locator"] == dict(published_page=547, pdf_page_index=2, figure="3")

    assert len(source_rows) == len(TABLE4_RUNS)
    assert {row["run_id_as_printed"] for row in source_rows} == TABLE4_RUNS.keys()
    for row in source_rows:
        _check_run_cells(row)

    for name, mixture in TABLE1_MIXTURES.items():
        assert values["composition_maps"][name] == dict(zip(OXIDES, mixture[:5], strict=True))
        assert values["composition_totals_as_printed"][name] == mixture[-2]
        assert values["silica_formula_x_as_printed"][name] == mixture[-1]
        assert values["composition_map_cell_locators"][name] == {
            oxide: _cell(546, 1, "1", name, oxide) for oxide in OXIDES
        }
        assert values["composition_total_wt_pct_locators"][name] == _cell(546, 1, "1", name, "Total")
        assert values["silica_formula_x_locators"][name] == _cell(546, 1, "1", name, "x")

    uncertainty = series["uncertainty_as_published"]
    assert (
        "For most iron-rich samples, shifting the fitted baseline by ±1σ gave an "
        "estimated absolute Δ(Fe3+/Fe2+) of about 0.05;"
    ) in uncertainty
    assert (
        "reprocessing the spectra gave a total absolute ratio error no higher than 0.1."
    ) in uncertainty
    assert "Individual row uncertainties are not printed." in uncertainty
    assert series["uncertainty_locator"] == dict(
        published_page=550, pdf_page_index=5,
        section="The effect of silica concentration on the Fe3+/Fe2+ ratio",
    )
    assert series["uncertainty_basis"] == (
        "absolute ratio change; baseline shift and reprocessing estimates, not per-row standard deviations"
    )
    assert series["figure_uncertainty_locator"] == dict(
        published_page=551, pdf_page_index=6, figure="7", series="iron-rich",
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        row for row in migrator.result.observations.values()
        if row.observation_id.startswith(f"{SOURCE_ID}::")
    ]
    assert len(migrated) == len(TABLE4_RUNS)
    assert {row.locator.record for row in migrated} == TABLE4_RUNS.keys()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()

    for observation in migrated:
        run = observation.locator.record
        temperature, ratio, name = TABLE4_RUNS[run]
        duration, _ = TABLE2_RUNS[temperature]
        wt_pct = dict(zip(OXIDES, map(Decimal, TABLE1_MIXTURES[name][:5]), strict=True))
        conditions = observation.point_conditions
        assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
        assert observation.value.point == Decimal(ratio)
        assert observation.uncertainty.kind.value == "none"
        assert conditions["composition"].state.value.as_map() == wt_pct_to_mole_fraction(wt_pct).as_map()
        for located, folio, index, table, record in (
            (observation, 549, 4, "4", run),
            (conditions["T_C"], 546, 1, "2", temperature),
            (conditions["duration_h"], 546, 1, "2", temperature),
            (conditions["atmosphere_as_printed"], 546, 1, "2", "note"),
            (conditions["total_pressure_atm"], 546, 1, None, "one atmosphere vertical tube furnace"),
            (conditions["composition"], 546, 1, "1", name),
        ):
            locator = located.locator
            assert locator is not None
            assert (locator.published_page, locator.pdf_page_index, locator.table, locator.record) == (
                folio, index, table, record,
            )
        assert conditions["T_C"].state.value == Decimal(temperature)
        assert conditions["duration_h"].state.value == Decimal(duration)
        assert conditions["atmosphere_as_printed"].state.value == "air"
        assert conditions["total_pressure_atm"].state.value == Decimal("1")
        assert observation.identity.total_pressure_Pa.value == Decimal("101325")

    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []


def test_borisov_2010_rejects_velocity_locator_moved_to_ratio_folio():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["Fe"]["observations"][0]["values"]["rows"]
    row = next(row for row in rows if row["run_id_as_printed"] == "DAFL-9")
    _check_run_cells(row)
    row["cell_locators"]["vmax_mm_s_as_printed"].update(published_page=549, pdf_page_index=4)
    with TestCase().assertRaisesRegex(AssertionError, "DAFL-9.*vmax_mm_s_as_printed"):
        _check_run_cells(row)
