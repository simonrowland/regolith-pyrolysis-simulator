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
    observations = payload["species"]["TiO2"]["observations"]
    gamma_rows = [
        row for row in observations
        if row["values"].get("quantity") == "activity_coefficient"
    ]
    assert len(gamma_rows) == 10
    assert gamma_rows[0]["values"]["gamma"] == 6.63
    assert gamma_rows[-1]["point_conditions"]["TiO2_wt_pct"]["state"]["value"] == 43.05
    assert all(row["values"]["method_class"] == "measured_reduced" for row in gamma_rows)
    assert all(row["derivation"]["inputs"] == row["derived_from"] for row in gamma_rows)
    table_3 = by_id["borisov_2004_table_3_reduced_redox_values"]
    assert table_3["values"]["rows"][0]["Ti4_over_Ti3"] == 26.39
    assert table_3["values"]["rows"][-1]["minus_log_fO2"] == 11.12
    table_4 = by_id["borisov_2004_table_4_karrooite_composition"]
    assert table_4["locator"] == {
        "page": 7, "pdf_page_index": 6, "published_page": 75, "table": "4"
    }
    assert table_4["values"]["rows"][0]["TiO2_wt_pct"] == 81.81
    assert table_4["values"]["rows"][-1]["D_Ti3plus_atomic"] == 23.3
    assert table_4["values"]["prose_bounds"]["CaO_wt_pct"] == [0.03, 0.68]
    figures = [row for row in context if row["type"] == "deferred_figure"]
    assert len(figures) == 6

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = tuple(migrator.result.observations.values())
    assert len(migrated) == 11
    gamma = [row for row in migrated if row.identity.quantity.value.value == "activity_coefficient"]
    assert len(gamma) == 10
    first = next(row for row in gamma if "dat32n_activity_coefficient" in row.observation_id)
    assert str(first.value.point) == "6.63"
    assert str(first.point_conditions["XTiO2"].state.value) == "0.1508"
    assert str(first.point_conditions["TiO2_wt_pct"].state.value) == "19.09"
    assert str(first.point_conditions["temperature_K"].state.value) == "1495"
    assert first.identity.composition.reason == "no composition mapped from source"
    assert sum(row.identity.quantity.value.value == "activity" for row in migrated) == 1
    assert not migrator.result.validation.hard_issues
