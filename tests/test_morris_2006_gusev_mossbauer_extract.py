from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "morris-2006-gusev-mossbauer"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"
TABLES = CORPUS / "tables" / SID

# Independent Table 5 transcription from 600 dpi renders of physical PDF pages 8–9.
RENDER_GRID = {
    ("A014SU0", "FirstSoil"): {
        "block_as_printed": "Plains, Gusev Crater",
        "Ol_area_pct": "34a", "Px_area_pct": "35", "Ilm_area_pct": "0", "npOx_area_pct": "23",
        "Fe3_sulfate_area_pct": "0", "Mt_area_pct": "6", "Mt_tet_area_pct": "3", "Mt_oct_area_pct": "3",
        "Hm_area_pct": "2", "Gt_area_pct": "0", "total_area_pct_as_printed": "100",
        "Fe3_over_FeT_as_printed": "0.29b", "T_low_K": "230", "T_high_K": "280", "pdf_page": 8,
    },
    ("A353RU0", "Champagne_Lip"): {
        "block_as_printed": "Cumberland Ridge, Husband Hill, Columbia Hills, Gusev Crater",
        "Ol_area_pct": "17c", "Px_area_pct": "29d", "Ilm_area_pct": "4", "npOx_area_pct": "17",
        "Fe3_sulfate_area_pct": "0", "Mt_area_pct": "10", "Mt_tet_area_pct": "8", "Mt_oct_area_pct": "3",
        "Hm_area_pct": "7", "Gt_area_pct": "16c", "total_area_pct_as_printed": "100",
        "Fe3_over_FeT_as_printed": "0.49", "T_low_K": "210", "T_high_K": "250", "pdf_page": 9,
    },
    ("A510RB0", "Backstay_Scuppler"): {
        "block_as_printed": "Cumberland Ridge, Husband Hill, Columbia Hills, Gusev Crater",
        "Ol_area_pct": "35", "Px_area_pct": "37", "Ilm_area_pct": "3", "npOx_area_pct": "13",
        "Fe3_sulfate_area_pct": "0", "Mt_area_pct": "11", "Mt_tet_area_pct": "5", "Mt_oct_area_pct": "6",
        "Hm_area_pct": "2", "Gt_area_pct": "0", "total_area_pct_as_printed": "100",
        "Fe3_over_FeT_as_printed": "0.23", "T_low_K": "210", "T_high_K": "270", "pdf_page": 9,
    },
}


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_table5_render_grid_binds_every_area_cell_to_target_block_and_temperature_window():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = source["species"]["Fe"]["context"]
    table5 = next(row for row in context if row["observation_id"] == "morris_2006_gusev_table5_context")
    rows = table5["values"]["rows"]
    assert len(rows) == 82
    actual = {(row["sample_id"], row["target_name"]): row for row in rows}
    assert len(actual) == len(rows)
    for key, expected in RENDER_GRID.items():
        row = actual[key]
        for field, value in expected.items():
            if field in row["area_cells_as_printed"]:
                observed = row["area_cells_as_printed"][field]
            else:
                observed = row[field]
            assert observed == value, (key, field, observed, value)
    assert table5["values"]["disposition_reason"] == (
        "whole-sample Mossbauer of a multiphase rock/soil; no melt-equilibrium temperature or fO2; "
        "not a melt redox observable (controller ruling 2026-10-20)"
    )
    assert "valence_ratio" in table5["values"]["candidate_tokens"]
    assert "derivation_note" not in table5["values"]
    assert table5["values"]["target_count_reported_in_text"] == 86
    assert table5["values"]["target_count_in_Table_5"] == 82
    assert "A472RU0" in table5["values"]["target_count_discrepancy_note"]
    assert "A472RB0" in table5["values"]["target_count_discrepancy_note"]
    table3 = next(row for row in context if row["observation_id"] == "morris_2006_gusev_table3_context")
    assert "A472RU0 (Keystone_Haunch)" in table3["values"]["printed_page_transcription"]


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_all_tables_have_dispositions_and_all_figures_are_deferred_without_points():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    body = source["species"]["Fe"]
    assert body["observations"] == []
    context = body["context"]
    tables = [row for row in context if row["type"] == "measured_table_context"]
    assert {row["observation_id"] for row in tables} == {
        f"morris_2006_gusev_table{number}_context" for number in range(1, 8)
    }
    assert all(row["values"]["candidate_tokens"] for row in tables)
    assert all(row["values"]["disposition_reason"] for row in tables)
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 13
    assert {row["locator"]["figure"] for row in figures} == {str(number) for number in range(1, 14)}
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
    figure2 = next(row for row in figures if row["locator"]["figure"] == "2")
    assert "210–70 K" in figure2["values"]["caption"]
    assert "210–270 K" in figure2["values"]["temperature_as_printed"]


@pytest.mark.skipif(not TABLES.is_dir(), reason=f"corpus table asset is unavailable: {TABLES}")
def test_derived_class_means_remain_context_linked_to_table5():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = source["species"]["Fe"]["context"]
    for table_number in (6, 7):
        row = next(
            item for item in context
            if item["observation_id"] == f"morris_2006_gusev_table{table_number}_context"
        )
        assert row["type"] == "measured_table_context"
        assert row["values"]["derived_from"] == [f"tables:{SID}"]
        assert row["values"]["derivation"]["inputs"] == [f"tables:{SID}"]
        assert "table-5.csv" in row["values"]["derivation"]["locator"]
        assert "remain context" in row["values"]["derivation"]["relation"]


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
