"""Pins the starting-material chemistry and scoped context for Krawczynski & Grove (2012)."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/krawczynski-grove-2012-high-ti-lunar-melts.yaml"


def test_krawczynski_2012_starting_compositions_and_migration() -> None:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = payload["species"]["SiO2"]["context"]
    by_id = {row["observation_id"]: row for row in context}
    table_1 = by_id["krawczynski_2012_table_1_starting_compositions"]
    rows = table_1["values"]["rows"]
    assert len(rows) == 4
    assert rows[0]["TiO2"] == 13.8
    assert rows[3]["TiO2"] == 8.78
    assert rows[1]["K2O"] == "—"
    table_2 = by_id["krawczynski_2012_table_2_a15r_conditions_and_partitioning"]["values"]
    assert table_2["row_count"] == 26
    assert "C321       Fe            1410" in table_2["table_text_as_printed"]
    assert "0.270" in table_2["table_text_as_printed"]
    table_3 = by_id["krawczynski_2012_table_3_a17o_conditions_and_partitioning"]["values"]
    assert "D176          Fe               1550" in table_3["table_text_as_printed"]
    table_4 = by_id["krawczynski_2012_table_4_a15r_phase_compositions"]
    assert table_4["locator"]["pdf_page_index"] == 7
    assert "35.1" in table_4["values"]["table_text_as_printed"]
    table_5 = by_id["krawczynski_2012_table_5_a17o_phase_compositions"]
    assert "Table 5" in table_5["values"]["table_text_as_printed"]
    figures = [row for row in context if row["type"] == "deferred_figure"]
    assert len(figures) == 10
    assert {row["locator"]["figure"] for row in figures} == {str(n) for n in range(1, 11)}
    assert all(not row["values"].get("points") for row in figures)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
