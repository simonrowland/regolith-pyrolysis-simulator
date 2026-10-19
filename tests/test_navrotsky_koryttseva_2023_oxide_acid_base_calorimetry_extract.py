from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry.yaml"
TABLE1_PINS = {
    "K2O": ("66.70", "78.17"),
    "BaO": ("35.55", "41.20"),
    "Na2O": ("56.52", "68.76"),
    "SrO": ("34.67", "40.21"),
    "Li2.5O": ("50.20", "58.52"),
    "CaO": ("33.55", "38.87"),
    "MgO": ("31.60", "36.71"),
    "ZrO2": ("47.07", "54.68"),
    "Ta2O5": ("110.71", "129.29"),
    "TiO2": ("46.26", "53.70"),
    "Nb2O5": ("108.00", "125.40"),
    "Al2O3": ("74.26", "86.68"),
    "V2O5": ("177.19", "196.25"),
    "Fe2O3": ("96.43", "110.94"),
    "CoO": ("36.25", "41.80"),
    "CuO": ("34.55", "40.07"),
    "SiO2": ("43.46", "50.37"),
    "B2O3": ("94.06", "107.03"),
    "MoO3": ("60.96", "71.56"),
    "P2O5": ("115.00", None),
    "PbO": ("36.86", "42.88"),
    "WO3": ("62.15", "73.61"),
    "SO3": ("45.43", "53.11"),
}
# Table 1 heat contents outside reference [28], read from the 600 dpi renders.
# A missing second value means the 800 °C cell is blank. ZnO's 800 °C cell
# prints 37.66 and no reference.
OUTSIDE_28 = {
    "La2O3": ("83.73", "97.43", "[30]", "[30]"),
    "CeO2": ("49.19", "57.33", "[31]", "[31]"),
    "Nd2O3": ("88.09", "102.78", "[30]", "[30]"),
    "Sm2O3": ("91.15", "106.17", "[30]", "[30]"),
    "Gd2O3": ("82.58", "95.86", "[30]", "[30]"),
    "Dy2O3": ("86.70", "100.39", "[30]", "[30]"),
    "Y2O3": ("81.19", "94.11", "[30]", "[30]"),
    "Ho2O3": ("84.13", "97.35", "[30]", "[30]"),
    "Er2O3": ("82.91", "96.13", "[30]", "[30]"),
    "Tm2O3": ("85.71", "99.06", "[30]", "[30]"),
    "Lu2O3": ("80.40", "93.61", "[30]", "[30]"),
    "ThO2": ("49.50", "57.5", "[33]", "[34]"),
    "HfO2": ("49.56", "57.78", "[30]", "[30]"),
    "Sc2O3": ("78.25", "91.21", "[30]", "[30]"),
    "Np2O5": ("109.26", None, "[33]", None),
    "UO3": ("64.22", None, "[33]", None),
    "Mn2O3": ("81.10", "94.75", "[31]", "[31]"),
    "ZnO": ("32.40", "37.66", "[31]", "not printed"),
    "CdO": ("33.64", "39.00", "[39]", "[39]"),
    "In2O3": ("81.11", "93.98", "[39]", "[39]"),
    "Ga2O3": ("77.45", "90.11", "[30]", "[30]"),
    "NiO": ("36.60", "42.10", "[31]", "[31]"),
    "SnO2": ("49.52", "57.78", "[31]", "[31]"),
    "GeO2": ("44.76", "52.29", "[30]", "[30]"),
    "Bi2O3": ("84.23", "96.89", "[31]", "[31]"),
}


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table1_reference28_heat_contents_match_600dpi_render():
    document = _document()
    assert len(document["fidelity_samples"]) == 94

    for formula, expected in TABLE1_PINS.items():
        rows = document["species"][formula]["observations"]
        assert len(rows) == 1
        row = rows[0]
        assert row["type"] == "heat_capacity_and_heat_content_series"
        assert row["phase"] in {"cr", "l", "g"}
        assert row["units"] == "kJ_per_mol"
        assert row["values"]["quantity"] == "H_minus_H298"
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"] == "Reference [28], as cited in Table 1."
        assert [point["enthalpy_increment"] for point in row["values"]["series"]] == [
            value for value in expected if value is not None
        ]
        assert all(point["reference"] == "[28]" for point in row["values"]["series"])

    assert check_all_fidelity_samples_match(document) == []
    mutated = deepcopy(document)
    mutated["species"]["K2O"]["observations"][0]["values"]["series"][0][
        "enthalpy_increment"
    ] = "66.71"
    assert check_all_fidelity_samples_match(mutated)


def test_table1_heat_contents_outside_reference_28_match_the_render():
    document = _document()
    for formula, (at_700, at_800, ref_700, ref_800) in OUTSIDE_28.items():
        rows = document["species"][formula]["observations"]
        assert len(rows) == 1
        series = rows[0]["values"]["series"]
        assert rows[0]["values"]["quantity"] == "H_minus_H298"
        assert rows[0]["values"]["method_class"] == "quoted_attributed"
        assert [point["enthalpy_increment"] for point in series] == [
            value for value in (at_700, at_800) if value is not None
        ]
        assert [point["reference"] for point in series] == [
            value for value in (ref_700, ref_800) if value is not None
        ]
    tho2 = document["species"]["ThO2"]["observations"][0]["values"]["series"][1]
    assert tho2["enthalpy_increment"] == "57.5"
    assert tho2["sigma"] == "1.7"
    teo2 = document["species"]["TeO2"]["observations"]
    assert [(row["phase"], row["values"]["series"][0]["enthalpy_increment"], row["values"]["series"][0]["state_as_published"]) for row in teo2] == [
        ("cr", "48.84", "solid"),
        ("l", "88.37", "liquid"),
    ]
    assert all(row["values"]["series"][0]["reference"] == "[39]" for row in teo2)


def test_isolated_migration_has_95_points_and_temperature_on_every_row():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 95
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    temperatures = {Decimal("973.15"), Decimal("1073.15")}
    counts = {temperature: 0 for temperature in temperatures}
    for row in result.observations.values():
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity.H_MINUS_H298
        assert row.identity.temperature_K.value in temperatures
        assert row.identity.species.phase.value in {Phase.CR, Phase.L, Phase.G}
        assert row.locator.table == "1"
        assert row.locator.page in {5, 6, 7}
        assert row.locator.pdf_page_index == row.locator.page - 1
        assert row.locator.published_page == row.locator.page
        counts[row.identity.temperature_K.value] += 1
    assert counts == {Decimal("973.15"): 49, Decimal("1073.15"): 46}


def test_tables_figures_and_all_nested_locators_are_preserved():
    document = _document()
    assert_all_physical_locators(document)

    context = document["species"]["SiO2"]["context"]
    table1, table2 = [row for row in context if row["type"] == "table_context"]
    assert "Table 1. Heat contents" in table1["values"]["printed_table_text"]
    assert "115.00 [28]" in table1["values"]["printed_table_text"]
    assert table1["locator"] == {
        "page": 5,
        "published_page": 5,
        "pdf_page_index": 4,
        "table": "1",
    }
    assert "−87.94 [48]" in table2["values"]["printed_table_text"]
    assert table2["locator"] == {
        "page": 9,
        "published_page": 9,
        "pdf_page_index": 8,
        "table": "2",
    }

    figures = {row["locator"]["figure"]: row for row in context if row["type"] == "figure_context"}
    assert set(figures) == {str(number) for number in range(1, 9)}
    for figure in figures.values():
        values = figure["values"]
        assert values["quantity"]
        assert values["identity"]
        assert values["panel"]
        assert values["axes"]
        assert values["attribution"]
        assert values["digitisation"] == "marker inventory checked at 600 dpi; numeric digitisation deferred"
        assert values["series"]
        assert all(
            series["name"] and series["marker"] and isinstance(series["marker_count"], int)
            and series["attribution"]
            for series in values["series"]
        )
