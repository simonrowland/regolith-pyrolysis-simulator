"""Pins the model context and deferred figures for Bonnell and Hastie (1990)."""

from __future__ import annotations

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/bonnell-hastie-1990-htsci-26-313.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table3_model_output_matches_rendered_cells() -> None:
    document = _document()
    context = document["species"]["SiO2"]["context"]
    table = next(
        row
        for row in context
        if row.get("observation_id") == "bonnell_hastie_1990_table3_model_output"
    )
    assert table["locator"]["pdf_page_index"] == 6
    assert table["temperature_K"] == 1430.00
    assert table["pressure_atm_as_printed"] == "1.000"
    assert table["values"]["method_class"] == "model_derived"

    rows = table["values"]["rows_as_printed"]
    assert len(rows) == 20
    by_species = {row["species"]: row for row in rows}
    assert by_species["K2Si2O5(l)"]["amount_mol"] == "1.56750E-01"
    assert by_species["K2Si2O5(l)"]["activity"] == "3.87176E-01"
    assert by_species["SiO(g)"]["delta_fG_T_kcal_mol"] == "-53.251"
    assert by_species["SiO(g)"]["log_Kp"] == "8.139"
    assert by_species["Si(s or l)"]["activity"] == "2.14860E-19^d"
    assert by_species["Na2Si2O5(s)"]["pressure_atm"] == ""
    assert by_species["K(g)"]["nominal_mol"] == "4.46000E-01^c"
    assert by_species["Ar(g)"]["pressure_atm"] == "9.99996E-01^c"
    assert set(table["footnotes_as_printed"]) == {"a", "b", "c", "d"}


def test_all_figure_panels_are_deferred_context_records_without_points() -> None:
    document = _document()
    contexts = document["species"]["SiO2"]["context"]
    figures = [row for row in contexts if row.get("type") == "figure_record"]

    assert len(figures) == 11
    assert {row["locator"]["figure"] for row in figures} == {
        "1", "2", "3", "4", "5", "6", "7a", "7b", "8", "9", "10"
    }
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["method_class"] == "model_derived" for row in figures)
    assert all(
        "points" not in row and "numeric_points" not in row
        for row in figures
    )
    assert all(
        isinstance(series["marker_count"], int)
        for row in figures
        for series in row["series"]
    )
    assert all(
        row["axes_as_printed"] and row["system_as_printed"] and row["phase_as_printed"]
        for row in figures
    )
    fig3 = next(row for row in figures if row["locator"]["figure"] == "3")
    assert fig3["composition_as_printed"] == (
        "0.116 Na2O + 0.223 K2O + 0.661 SiO2"
    )
    assert {
        row["locator"]["figure"]: row["locator"]["pdf_page_index"]
        for row in figures
    } == {
        "1": 9, "2": 10, "3": 11, "4": 12, "5": 13,
        "6": 14, "7a": 15, "7b": 15, "8": 17, "9": 18, "10": 19,
    }


def test_isolated_migration_does_not_score_model_context_or_deferred_figures() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert not result.validation.hard_issues
    assert not result.observations
    assert len(result.queue) == 1
    assert result.queue[0].axes == ["document"]
    assert result.queue[0].why == "model-derived cohort excluded by ruling"
    assert result.queue[0].observation_id == (
        "bonnell-hastie-1990-htsci-26-313::typed_absence"
    )
