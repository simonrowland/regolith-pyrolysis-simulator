from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "colson-colson-1995-chromium-dimer-lpsc"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_lpsc_abstract_keeps_plotted_results_deferred() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    assert document["source"]["year"] == 1995
    assert "Lunar and Planetary Science Conference XXVI, 269–270" in document["source"]["citation"]

    chromium = document["species"]["Cr"]
    assert chromium["observations"] == []
    interpretation = chromium["context"][0]
    assert interpretation["locator"] == {"page": 1, "published_page": 269, "pdf_page_index": 0, "section": "abstract"}
    assert "partly as a dimer" in interpretation["values"]["interpretation_as_printed"]
    assert "no printed numeric Cr redox" in interpretation["reason"]

    figures = [row for species in document["species"].values() for row in species["context"] if row["type"] == "figure_record"]
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6"}
    assert all(row["locator"]["page"] == 2 and row["locator"]["pdf_page_index"] == 1 for row in figures)
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["points"] == [] for row in figures)

    chromium_figures = [row for row in chromium["context"] if row["type"] == "figure_record"]
    assert [row["values"]["temperature_as_printed"] for row in chromium_figures] == [
        "1425 °C and 1575 °C series",
        "1425 °C",
        "1425 °C; average of several experiments",
        "not stated in panel",
    ]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
