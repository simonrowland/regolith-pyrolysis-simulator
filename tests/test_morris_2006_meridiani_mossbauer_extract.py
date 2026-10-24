from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "morris-2006-meridiani-mossbauer"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"
TABLES = CORPUS / "tables" / SID

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
    assert table3["values"]["disposition_reason"] == (
        "whole-sample Mossbauer of a multiphase rock/soil; no melt-equilibrium temperature or fO2; "
        "not a melt redox observable (controller ruling 2026-10-20)"
    )
    assert "valence_ratio" in table3["values"]["candidate_tokens"]
    assert "derivation_note" not in table3["values"]
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


@pytest.mark.skipif(not TABLES.is_dir(), reason=f"corpus table assets are unavailable: {TABLES}")
def test_derived_class_means_remain_context_linked_to_registered_tables():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = source["species"]["Fe"]["context"]
    table7 = next(
        row for row in context
        if row["observation_id"] == "morris_2006_meridiani_table7_context"
    )
    assert table7["type"] == "measured_table_context"
    assert table7["values"]["derived_from"] == [f"tables:{SID}"]
    assert table7["values"]["derivation"]["inputs"] == [f"tables:{SID}"]
    assert "table-3.csv" in table7["values"]["derivation"]["locator"]
    assert "remain context" in table7["values"]["derivation"]["relation"]
    table10 = next(
        row for row in context
        if row["observation_id"] == "morris_2006_meridiani_table10_context"
    )
    assert table10["values"]["derived_from"] == [f"tables:{SID}"]
    assert table10["values"]["derivation"]["inputs"] == [f"tables:{SID}"]
    assert "table-9.csv" in table10["values"]["derivation"]["locator"]
    relation = table10["values"]["derivation"]["relation"]
    disposition = table10["values"]["disposition_reason"]
    assert 'Calculated from elemental data in Table 9.' in relation
    assert 'Calculated from elemental data in Table 9.' in disposition
    assert "copy" not in disposition.lower()
    assert "copied" not in disposition.lower()
    assert "arithmetic means of member target values" not in relation
    assert "copies" not in relation
    assert "remain context" in relation
    assert "OCBP1" in table10["values"]["disposition_reason"]

    with (TABLES / "table-9.csv").open(newline="", encoding="utf-8") as table_file:
        csv_row = next(row for row in csv.DictReader(table_file) if row["quantity_as_printed"] == "Fe3+/FeT")
    page_cell = "0.39 ± 0.03"
    assert csv_row["Martian_Dust_average_percent_as_printed"] == page_cell


@pytest.mark.skipif(not PDF.is_file() or not TEXT.is_file(), reason=f"corpus source is unavailable: {SID}")
def test_context_only_extract_migrates_no_unavailable_scored_observations():
    index = {
        SID: {
            "corpus": {
                "raw": {"path": str(PDF), "present": PDF.is_file()},
                "text": {"path": str(TEXT), "exists": TEXT.is_file()},
                "tables": {"path": str(TABLES), "exists": TABLES.is_dir()},
            }
        }
    }
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert observations == []
    assert not [row for row in observations if row.value.kind is ValueKind.UNAVAILABLE]
    assert not migrator.result.validation.hard_issues
    work = next(work for work in migrator.result.works.values() if SID in work.source_ids)
    table_asset = next(asset for asset in work.source_files.files if asset.asset_id == f"tables:{SID}")
    assert table_asset.path == str(TABLES)
