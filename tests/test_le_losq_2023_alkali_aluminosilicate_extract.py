from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "le-losq-2023-alkali-aluminosilicate-glasses"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Independent transcriptions from the 600 dpi physical-page 7 render.
TABLE_1_RENDER = [
    ("Viscosity, log10 Pa·s (Adam-Gibbs theory)", "RMSE", "0.2", "0.3", "0.4"),
    ("Viscosity, log10 Pa·s (Adam-Gibbs theory)", "MAE", "0.1", "0.1", "0.1"),
    ("Viscosity, log10 Pa·s (Free Volume theory)", "RMSE", "0.2", "0.2", "0.3"),
    ("Viscosity, log10 Pa·s (Free Volume theory)", "MAE", "0.1", "0.1", "0.1"),
    ("Viscosity, log10 Pa·s (Vogel-Tamman-Fulcher equation)", "RMSE", "0.2", "0.3", "0.3"),
    ("Viscosity, log10 Pa·s (Vogel-Tamman-Fulcher equation)", "MAE", "0.1", "0.1", "0.1"),
    ("Viscosity, log10 Pa·s (MYEGA equation)", "RMSE", "0.2", "0.3", "0.3"),
    ("Viscosity, log10 Pa·s (MYEGA equation)", "MAE", "0.1", "0.1", "0.1"),
    ("Viscosity, log10 Pa·s (Avramov-Milchev theory)", "RMSE", "0.2", "0.3", "0.3"),
    ("Viscosity, log10 Pa·s (Avramov-Milchev theory)", "MAE", "0.1", "0.1", "0.1"),
    ("Density, g cm−3", "RMSE", "0.01", "0.01", "0.02"),
    ("Density, g cm−3", "MAE", "0.004", "0.006", "0.006"),
    ("Raman spectra, % median absolute percentage error", "MAPE", "17", "17", "25"),
    ("Refractive index", "RMSE", "0.003", "0.005", "0.006"),
    ("Refractive index", "MAE", "0.0009", "0.0009", "0.0013"),
    ("CTE, 10−6 K−1", "RMSE", "1.0", "0.9", "1.1"),
    ("CTE, 10−6 K−1", "MAE", "0.4", "0.5", "0.5"),
    ("Elastic modulus, GPa", "RMSE", "4", "8", "6"),
    ("Elastic modulus, GPa", "MAE", "2", "2", "2"),
    ("Abbe number", "RMSE", "1.1", "0.4", "3.7"),
    ("Abbe number", "MAE", "0.3", "0.2", "0.5"),
    ("Liquidus temperature, K", "RMSE", "61", "66", "60"),
    ("Liquidus temperature, K", "MAE", "38", "39", "39"),
    ("Glass transition temperature at 10^12 Pa·s, K", "RMSE", "13", "16", "12"),
    ("Glass transition temperature at 10^12 Pa·s, K", "MAE", "5", "6", "9"),
    ("Heat capacity, J mol−1 K−1", "RMSE", "2", "3", "3"),
    ("Heat capacity, J mol−1 K−1", "MAE", "1", "3", "1"),
    ("Glass entropy, J mol−1 K−1", "RMSE", "0.5", "1.4", "0.8"),
    ("Glass entropy, J mol−1 K−1", "MAE", "0.3", "0.8", "0.3"),
]

# Independent transcriptions from the 600 dpi physical-page 13 render.
TABLE_2_RENDER = [
    ("Viscosity", "90 %", "94 %"),
    ("Density", "87 %", "87 %"),
    ("Raman spectra", "70 %", "97 %"),
    ("Refractive index", "90 %", "97 %"),
    ("CTE", "84 %", "97 %"),
    ("Elastic modulus", "76 %", "94 %"),
    ("Abbe number", "81 %", "78 %"),
    ("Liquidus temperature", "85 %", "96 %"),
    ("Heat capacity", "57 %", "86 %"),
]

# Independent transcriptions from the 600 dpi physical-page 14 render.
TABLE_3_RENDER = [
    ("SiO2", ("24.1", "25.7", "27.1"), "26.0–27.5", "80 ± 2", "81.37"),
    ("Al2O3", ("34.5", "37.8", "38.4"), "37.0–39.0", "118 ± 5 + 0.028 ± 0.006 × T", "130.2 + 0.0357 × T"),
    ("Na2O", ("21.3", "22.9", "29.1"), "25.0–29.0", "99 ± 7", "100.6"),
    ("K2O", ("34.7", "36.9", "50.1"), "40.0–46.0", "71 ± 14 + 0.017 ± 0.005 × T", "50.13 + 0.01578 × T"),
    ("MgO", ("8.1", "13.1", "16.5"), "11.0–13.0", "81 ± 8", "85.78"),
    ("CaO", ("14.0", "14.9", "18.7"), "14.0–18.0", "92 ± 11", "86.05"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _context(document: dict, observation_id: str) -> dict:
    return next(
        item
        for body in document["species"].values()
        for item in body.get("context", [])
        if item["observation_id"] == observation_id
    )


def _table1_rows(document: dict) -> list[tuple]:
    context = _context(document, "le_losq_2023_table1_model_error_metrics")
    assert context["locator"]["page"] == 7
    assert context["locator"]["pdf_page_index"] == 6
    return [
        (
            row["property"],
            row["metric"],
            row["training"],
            row["validation"],
            row["testing"],
        )
        for row in context["values"]["rows"]
    ]


def _table2_rows(document: dict) -> list[tuple]:
    context = _context(document, "le_losq_2023_table2_prediction_interval_coverage")
    assert context["locator"]["page"] == 13
    assert context["locator"]["pdf_page_index"] == 12
    return [
        (
            row["dataset"],
            row["MC_dropout"],
            row["conformal_confidence_interval"],
        )
        for row in context["values"]["rows"]
    ]


def _table3_rows(document: dict) -> list[tuple]:
    context = _context(document, "le_losq_2023_table3_partial_molar_properties_context")
    assert context["locator"]["page"] == 14
    assert context["locator"]["pdf_page_index"] == 13
    return [
        (
            row["oxide"],
            tuple(row["Vm_quantiles_as_printed"]),
            row["Vm_literature_as_printed"],
            row["Cpliquid_model_as_printed"],
            row["Cpliquid_literature_as_printed"],
        )
        for row in context["values"]["rows"]
    ]


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_transcribed_tables_are_preserved_as_unscored_context() -> None:
    document = _document()
    assert _table1_rows(document) == TABLE_1_RENDER
    assert _table2_rows(document) == TABLE_2_RENDER
    assert _table3_rows(document) == TABLE_3_RENDER

    table1 = _context(document, "le_losq_2023_table1_model_error_metrics")
    table2 = _context(document, "le_losq_2023_table2_prediction_interval_coverage")
    table3 = _context(document, "le_losq_2023_table3_partial_molar_properties_context")
    assert table1["values"]["row_count"] == 29
    assert table1["values"]["numeric_cells"] == 87
    assert table2["values"]["row_count"] == 9
    assert table2["values"]["numeric_cells"] == 18
    assert table3["values"]["row_count"] == 6
    assert table3["values"]["data_cells"] == 36
    assert all(body["observations"] == [] for body in document["species"].values())


def test_mutating_a_transcribed_table_cell_breaks_the_render_pin() -> None:
    document = _document()
    changed = deepcopy(document)
    table1 = _context(changed, "le_losq_2023_table1_model_error_metrics")
    table1["values"]["rows"][0]["testing"] = "0.3"
    with pytest.raises(AssertionError):
        assert _table1_rows(changed) == TABLE_1_RENDER


def test_all_figures_are_deferred_and_keep_their_printed_identity() -> None:
    document = _document()
    figures = [
        item
        for body in document["species"].values()
        for item in body.get("context", [])
        if item["type"] == "figure_record"
    ]
    assert len(figures) == 10
    for figure in figures:
        locator = figure["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        assert locator.get("figure")
        assert figure.get("panel")
        assert figure.get("attribution")
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert figure["values"].get("caption")
        assert figure["values"].get("axes_as_printed")
        assert figure["values"].get("species_as_printed")
        assert figure["values"].get("phase_as_printed")
        assert figure["values"].get("temperature_as_printed")
        assert figure["values"].get("numeric_points") is None
        assert figure["values"].get("points") is None
        assert figure["values"]["series"]
        assert all("marker_count" in series for series in figure["values"]["series"])

    density = _context(document, "le_losq_2023_figure8_density_prediction_and_uncertainty")
    assert density["values"]["quantity_by_panel"] == {"a": "density", "b": "unavailable"}
    assert "confidence interval" in density["values"]["schema_gap"]


def test_quoted_partial_cp_is_context_and_isolated_migration_is_empty() -> None:
    document = _document()
    quote = _context(document, "le_losq_2023_text_quoted_partial_cp_al_coordination")
    assert quote["attribution"] == "[116]"
    assert quote["values"]["method_class"] == "quoted_attributed"
    assert quote["values"]["temperature_as_printed"] == "300 K"
    assert quote["values"]["coordination_number_4_Cp_J_mol-1_K-1_as_printed"] == "80.3"
    assert quote["values"]["coordination_number_5_Cp_J_mol-1_K-1_as_printed"] == "79.9"
    assert quote["values"]["coordination_number_6_Cp_J_mol-1_K-1_as_printed"] == "70.0"

    migrator = _migrate()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
