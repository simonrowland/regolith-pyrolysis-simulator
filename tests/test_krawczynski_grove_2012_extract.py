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

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
