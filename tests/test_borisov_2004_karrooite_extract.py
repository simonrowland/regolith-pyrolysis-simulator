"""Pins printed experimental and melt-activity tables for Borisov et al. (2004)."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-2004-karrooite-liquidus-redox.yaml"


def test_borisov_2004_tables_and_isolated_migration() -> None:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = payload["species"]["TiO2"]["context"]
    by_id = {row["observation_id"]: row for row in context}
    table_2 = by_id["borisov_2004_table_2_tio2_activity_coefficients"]
    assert len(table_2["values"]["rows"]) == 10
    assert table_2["values"]["rows"][0]["gamma_TiO2"] == 6.63
    assert table_2["values"]["rows"][-1]["TiO2_wt_pct"] == 43.05
    table_3 = by_id["borisov_2004_table_3_reduced_redox_values"]
    assert table_3["values"]["rows"][0]["Ti4_over_Ti3"] == 26.39
    assert table_3["values"]["rows"][-1]["minus_log_fO2"] == 11.12

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
