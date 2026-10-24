"""Migration pins for the scanned Kirschen et al. (1999) Table 4."""

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kirschen-1999-si-ti-al-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def test_table_4_render_grid_migrates_all_components_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    # Independent transcription from the 600 dpi render of physical PDF p. 10.
    # Each value is (printed wt%, printed 1σ); None is the printed blank cell.
    grid = {
        "ST 1-1H": {
            "temperature": ("2500", "30"), "rows": [
                ("99.66", "49", {"SiO2": ("57.49", "1.20"), "TiO2": ("42.16", "1.20"), "Al2O3": None}),
                ("100.80", "47", {"SiO2": ("28.70", "5.34"), "TiO2": ("72.10", "5.23"), "Al2O3": None}),
            ]},
        "ST 1-2H": {
            "temperature": ("2280", "30"), "rows": [
                ("100.28", "50", {"SiO2": ("67.91", "2.58"), "TiO2": ("32.37", "2.28"), "Al2O3": None}),
                ("100.24", "28", {"SiO2": ("5.55", "1.79"), "TiO2": ("94.51", "1.72"), "Al2O3": None}),
            ]},
        "ST 2-1H": {
            "temperature": ("2250", "30"), "rows": [
                ("100.10", "67", {"SiO2": ("74.61", "5.97"), "TiO2": ("25.49", "6.21"), "Al2O3": None}),
                ("100.84", "30", {"SiO2": ("18.47", "2.45"), "TiO2": ("82.23", "3.04"), "Al2O3": None}),
            ]},
        "ST 3-1H": {
            "temperature": ("2200", "30"), "rows": [
                ("100.21", "43", {"SiO2": ("68.81", "1.46"), "TiO2": ("31.40", "1.49"), "Al2O3": None}),
                ("100.76", "47", {"SiO2": ("10.10", "5.50"), "TiO2": ("90.62", "5.58"), "Al2O3": None}),
            ]},
        "STA1-1H": {
            "temperature": ("2080", "30"), "rows": [
                ("100.25", "63", {"SiO2": ("55.66", "1.57"), "TiO2": ("40.36", "1.67"), "Al2O3": ("4.22", "0.95")}),
                ("100.99", "28", {"SiO2": ("10.90", "1.51"), "TiO2": ("85.68", "1.63"), "Al2O3": ("4.41", "0.87")}),
            ]},
        "STA1-2H": {
            "temperature": ("2070", "30"), "rows": [
                ("100.56", "14", {"SiO2": ("55.34", "0.83"), "TiO2": ("40.96", "1.04"), "Al2O3": ("4.26", "0.64")}),
                ("100.80", "29", {"SiO2": ("11.53", "3.00"), "TiO2": ("84.78", "3.04"), "Al2O3": ("4.44", "1.05")}),
            ]},
        "STA2-2H¹": {
            "temperature": ("2050", "30"), "rows": [
                ("100.11", "36", {"SiO2": ("13.64", "0.53"), "TiO2": ("72.70", "0.66"), "Al2O3": ("13.77", "0.39")}),
            ]},
        "STA3-1H¹": {
            "temperature": ("2070", "30"), "rows": [
                ("100.27", "33", {"SiO2": ("33.12", "0.35"), "TiO2": ("53.71", "1.05"), "Al2O3": ("13.43", "0.15")}),
            ]},
        "STA3-2H¹": {
            "temperature": ("2050", "30"), "rows": [
                ("99.93", "40", {"SiO2": ("33.05", "0.31"), "TiO2": ("53.70", "0.59"), "Al2O3": ("13.17", "0.16")}),
            ]},
        "STA4-2H¹": {
            "temperature": ("1970", "30"), "rows": [
                ("100.01", "58", {"SiO2": ("51.53", "0.73"), "TiO2": ("34.65", "0.54"), "Al2O3": ("13.82", "0.17")}),
            ]},
        "STA5-2H¹": {
            "temperature": ("2230", "30"), "rows": [
                ("100.88", "10", {"SiO2": ("59.24", "0.58"), "TiO2": ("19.81", "0.44"), "Al2O3": ("21.83", "0.33")}),
            ]},
    }

    source_rows = {
        row["observation_id"]: (formula, row)
        for formula, species in document["species"].items()
        for row in species.get("observations", [])
    }
    assert len(source_rows) == len(result.observations) == 51
    assert len(document["experiments"]) == len(result.experiments) == 11
    assert len(grid) == 11

    points = unavailable = 0
    for sample, sample_data in grid.items():
        temperature, temperature_sigma = sample_data["temperature"]
        for phase_order, (printed_sum, n_measurements, components) in enumerate(sample_data["rows"], 1):
            for formula, expected in components.items():
                observation_id = (
                    f"kirschen_1999_t4_{sample.replace('¹', '').lower().replace(' ', '_').replace('-', '_')}"
                    f"_phase{phase_order}_{formula.lower()}"
                )
                raw_formula, raw = source_rows[observation_id]
                values = raw["values"]
                observation = result.observations[f"{SOURCE_ID}::{observation_id}"]
                assert raw_formula == "SiO2"  # Filing container carries the multicomponent system.
                assert values["formula"] == formula
                assert observation.identity.species.formula == formula
                assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
                assert observation.identity.species.phase.value.value == "glass"
                assert observation.identity.phase_role.value.value == "glass"
                assert observation.identity.subtype.value == "wt_percent"
                assert observation.identity.temperature_K.value == Decimal(temperature)
                assert observation.point_conditions["temperature_K"].state.value == Decimal(temperature)
                assert observation.point_conditions["temperature_K"].locator.page == 10
                assert observation.point_conditions["temperature_K"].locator.pdf_page_index == 9
                assert observation.point_conditions["gas_mix"].state.value == "pure oxygen"
                assert observation.point_conditions["gas_mix"].locator.page == 9
                assert observation.point_conditions["gas_mix"].locator.pdf_page_index == 8
                assert observation.point_conditions["printed_sum"].state.value == Decimal(printed_sum)
                assert Decimal(str(values["printed_sum"])) == Decimal(printed_sum)
                assert values["n_measurements"] == n_measurements
                assert values["sample_id_as_printed"] == sample
                assert values["phase_order"] == phase_order
                assert observation.locator.page == 10
                assert observation.locator.pdf_page_index == 9
                assert values["T_K"]["sigma"] == temperature_sigma
                if expected is None:
                    unavailable += 1
                    assert values["status"] == values["admission_status"] == "typed_refusal"
                    assert "blank" in values["reason"]
                    assert values["printed_cell_as_published"] == ""
                    assert observation.value.kind.value == "unavailable"
                else:
                    points += 1
                    amount, sigma = expected
                    assert values["component_amount"] == {"value": amount, "sigma": sigma}
                    assert observation.value.kind.value == "point"
                    assert Decimal(str(observation.value.point)) == Decimal(amount)
                    assert Decimal(str(raw["uncertainty"]["sigma"])) == Decimal(sigma)
                    assert sigma not in {"0", "0.0"}
                    assert not observation.notices
    assert points == 43
    assert unavailable == 8


def test_figure_records_defer_values_and_preserve_figure_metadata() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [
        row for row in document["species"]["SiO2"]["context"]
        if row.get("type") == "figure_context"
    ]
    assert len(figures) == 7  # Figure 2 has separately identified panels a and b.
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6"}
    by_panel = {
        (row["locator"]["figure"], row["values"]["panel_as_printed"]): row["values"]
        for row in figures
    }
    assert by_panel[("1", "whole figure")]["axes_as_printed"] == (
        "x: SiO2 content [wt%]; y: temperature [K]"
    )
    assert by_panel[("1", "whole figure")]["basis"] == "wt_percent"
    assert by_panel[("2", "panel a")]["axes_as_printed"] == (
        "x: x(Al2O3); y: temperature [K]"
    )
    assert by_panel[("2", "panel b")]["axes_as_printed"] == (
        "x: x(TiO2); y: temperature [K]"
    )
    assert [series["marker_count"] for series in by_panel[("6", "whole figure")]["series"][-2:]] == [2, 3]
    assert "basis" not in by_panel[("4", "whole image")]
    table2 = next(
        row for row in document["species"]["SiO2"]["context"]
        if row.get("observation_id") == "kirschen_1999_table_2_context"
    )
    assert any("0.00024607" in footnote and "373K" in footnote and "848K" in footnote
               for footnote in table2["values"]["footnotes_as_printed"])
    for row in figures:
        values = row["values"]
        assert values["quantity"] == "equilibrium_phase_composition"
        assert values["species_as_printed"]
        assert values["phase_role"] in {"melt", "glass"}
        assert values["axes_as_printed"]
        assert values["caption_as_printed"].startswith("Fig.")
        assert values["panel_as_printed"]
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["admission_status"] == "figure_only"
