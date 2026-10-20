from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kramer-1991-glass-basicity-gas-solubility"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_tables_and_figures_are_context_only_with_printed_values_preserved() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    species = document["species"]["SiO2"]
    assert species["observations"] == []
    context = species["context"]

    table_1 = next(row for row in context if row["observation_id"] == "kramer_1991_table_1_acid_values")
    assert len(table_1["values"]["rows"]) == 20
    assert table_1["values"]["rows"][0]["oxide"] == "B2O3"
    assert table_1["values"]["rows"][0]["dietzel"] == "1.62 to 1.45"
    assert table_1["values"]["rows"][0]["sun"] == 119
    assert table_1["values"]["rows"][3]["duffy_ingram"] == "unreported"

    table_2 = next(row for row in context if row["observation_id"] == "kramer_1991_table_2_glass_basicities")
    assert [row["B"] for row in table_2["values"]["rows"]] == [0, 13, 19.8, 30.2, 31.6, 34.2, 15.6, 20.4, 35.6]
    assert table_2["values"]["derivation_status"] == "derivation pending a corpus table asset (controller, t-1382)"

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 9
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"].get("points") == [] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6", "7", "8", "9"}

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
