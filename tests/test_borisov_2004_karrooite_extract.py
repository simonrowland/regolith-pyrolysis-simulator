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
    table_4 = by_id["borisov_2004_table_4_karrooite_composition"]
    assert table_4["locator"] == {"page": 7, "pdf_page_index": 6, "published_page": 75, "table": "4"}
    assert len(table_4["values"]["rows"]) == 12
    assert table_4["values"]["rows"][0]["TiO2_wt_pct"] == 81.81
    assert table_4["values"]["rows"][-1]["D_Ti3plus_atomic_ratio"] == 23.3
    assert table_4["values"]["prose_bounds"]["CaO_wt_pct"] == "0.03–0.68"
    figures = [row for row in context if row["type"] == "deferred_figure"]
    assert len(figures) == 6
    assert {row["locator"]["figure"] for row in figures} == {str(n) for n in range(1, 7)}
    assert all(not row["values"].get("points") for row in figures)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    observations = list(migrator.result.observations.values())
    activity = [row for row in observations if row.identity.quantity.value.value == "activity"]
    gamma = [row for row in observations if row.identity.quantity.value.value == "activity_coefficient"]
    assert len(activity) == 1
    assert activity[0].value.point == 1
    assert len(gamma) == 10
    assert [float(row.value.point) for row in gamma] == [6.63, 6.24, 5.93, 5.09, 4.89, 4.20, 4.03, 3.35, 3.05, 2.80]
    assert gamma[0].identity.temperature_K.value == 1495
    assert gamma[0].point_conditions["composition"].state.reason == "Di_An_eutectic_components_not_printed"
    assert all(row.identity.temperature_K.is_value for row in gamma)
    assert all(row.point_conditions["composition"].locator.page == 3 for row in gamma)
    assert all(row.point_conditions["temperature_C"].locator.table == "2" for row in gamma)
