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
    assert by_id["krawczynski_2012_table_4_a15r_phase_compositions"]["locator"]["pdf_page_index"] == 7
    table_2 = by_id["krawczynski_2012_table_2_a15r_conditions_and_partitioning"]["values"]
    assert table_2["row_count"] == 26
    assert "C321       Fe            1410       2" in table_2["printed_cells"]
    assert "0.270" in table_2["printed_cells"]
    table_4 = by_id["krawczynski_2012_table_4_a15r_phase_compositions"]["values"]
    assert "C321       glass      29    35.1" in table_4["printed_cells"]
    table_5 = by_id["krawczynski_2012_table_5_a17o_phase_compositions"]["values"]
    assert "C409" in table_5["printed_cells"]
    assert len([row for row in context if row["type"] == "deferred_figure"]) == 10

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
