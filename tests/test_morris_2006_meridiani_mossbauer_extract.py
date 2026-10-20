from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SID = "morris-2006-meridiani-mossbauer"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"

# Independent transcription from the 600 dpi Table 3 renders, physical PDF pages 8–9.
RENDER_GRID = {
    ("B011SU0", "Merlot_Tarmac"): {
        "block_as_printed": "Eagle Crater, Meridiani Planum",
        "Ol_area_pct": "39a", "Px_area_pct": "37", "npOx_area_pct": "14",
        "Jar_area_pct": "0", "Fe3D3_area_pct": "0", "Mt_area_pct": "6b",
        "Mt3_area_pct": "2", "Mt2_5_area_pct": "4", "Hm_area_pct": "4b",
        "a_FeNi0_metal_area_pct": "0", "sum_pct": "100c", "Fe3_over_FeT": "0.22d",
        "T_low_K": "190", "T_high_K": "280", "pdf_page": 8,
    },
    ("B067RU0", "BounceRock_Eifel"): {
        "block_as_printed": "Eagle-Endurance Intercrater Plains, Meridiani Planum",
        "Ol_area_pct": "0", "Px_area_pct": "99", "npOx_area_pct": "1",
        "Jar_area_pct": "0", "Fe3D3_area_pct": "0", "Mt_area_pct": "0",
        "Mt3_area_pct": "0", "Mt2_5_area_pct": "0", "Hm_area_pct": "0",
        "a_FeNi0_metal_area_pct": "0", "sum_pct": "100", "Fe3_over_FeT": "0.01",
        "T_low_K": "250", "T_high_K": "260", "pdf_page": 8,
    },
    ("B067RU0", "BounceRock_Loreley"): {
        "block_as_printed": "Eagle-Endurance Intercrater Plains, Meridiani Planum",
        "Ol_area_pct": "0", "Px_area_pct": "100", "npOx_area_pct": "0",
        "Jar_area_pct": "0", "Fe3D3_area_pct": "0", "Mt_area_pct": "0",
        "Mt3_area_pct": "0", "Mt2_5_area_pct": "0", "Hm_area_pct": "0",
        "a_FeNi0_metal_area_pct": "0", "sum_pct": "100", "Fe3_over_FeT": "0.00",
        "T_low_K": "250", "T_high_K": "260", "pdf_page": 8,
    },
    ("B557RB0", "FruitBasket_LemonRind"): {
        "block_as_printed": "Endurance-Erebus Intercrater Plains, Meridiani Planum",
        "Ol_area_pct": "6", "Px_area_pct": "13", "npOx_area_pct": "0",
        "Jar_area_pct": "33", "Fe3D3_area_pct": "18", "Mt_area_pct": "0",
        "Mt3_area_pct": "0", "Mt2_5_area_pct": "0", "Hm_area_pct": "30",
        "a_FeNi0_metal_area_pct": "0", "sum_pct": "100", "Fe3_over_FeT": "0.81",
        "T_low_K": "210", "T_high_K": "270", "pdf_page": 9,
    },
}


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_table3_render_grid_binds_phase_areas_target_and_temperature_window():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = source["species"]["Fe"]["context"]
    table3 = next(row for row in context if row["observation_id"] == "morris_2006_meridiani_table3_target_rows_context")
    rows = table3["values"]["rows"]
    assert len(rows) == 94
    actual = {(row["sample_id"], row["target_name"]): row for row in rows}
    assert set(actual) >= set(RENDER_GRID)
    for key, expected in RENDER_GRID.items():
        for field, value in expected.items():
            assert actual[key][field] == value, (key, field, actual[key][field], value)
    assert table3["values"]["derivation_note"] == "derivation pending a corpus table asset (controller)"
    assert "not a point" in table3["values"]["disposition_reason"]
    assert table3["values"]["target_count_in_Table_3"] == 94
    assert table3["values"]["target_count_reported_in_text"] == 95
    assert "B033RR0" in table3["values"]["target_count_discrepancy_note"]


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_all_tables_have_a_disposition_and_figures_are_deferred_without_points():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    body = source["species"]["Fe"]
    assert body["observations"] == []
    context = body["context"]
    tables = {
        row["observation_id"]
        for row in context
        if row["observation_id"].startswith("morris_2006_meridiani_table")
    }
    assert tables == {f"morris_2006_meridiani_table{number}_context" for number in (1, 2, 4, 5, 6, 7, 8, 9, 10)} | {"morris_2006_meridiani_table3_target_rows_context"}
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 10
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["species"] == "Fe"
        assert values["phase_as_printed"]
        assert values["temperature_as_printed"]
        assert values["caption"].startswith(f"Figure {figure['locator']['figure']}.")
        assert "points" not in values
        assert len(values["marker_count_per_series"]) == len(values["series"])
        assert all(isinstance(count, int) or isinstance(count, str) for count in values["marker_count_per_series"])
