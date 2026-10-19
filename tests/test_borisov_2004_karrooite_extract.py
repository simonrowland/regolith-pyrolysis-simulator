"""Pins printed experimental and melt-activity tables for Borisov et al. (2004)."""

from pathlib import Path
import copy
from decimal import Decimal

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


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
    assert "borisov_2004_table_3_reduced_redox_values" not in by_id
    assert "phase_assemblage" in by_id[
        "borisov_2004_table_1_redox_conditions_and_liquidus_phases"
    ]["values"]["reason"]
    table_3_tio2 = [
        row for row in observations
        if row["observation_id"].startswith("borisov_2004_table_3_")
    ]
    assert len(table_3_tio2) == 20
    assert sum(
        row["values"]["method_class"] == "measured_direct"
        for row in table_3_tio2
    ) == 10
    assert sum(
        row["values"]["method_class"] == "measured_reduced"
        for row in table_3_tio2
    ) == 10
    for row in table_3_tio2:
        assert row["values"]["T_C"] == 1300
        assert row["point_conditions"]["fO2_log"]["state"]["tag"] == "value"
        assert row["point_conditions"]["quench_state"]["state"]["value"] == "not_stated"
        if row["values"]["method_class"] == "measured_reduced":
            assert row["derivation"]["inputs"] == row["derived_from"]

    ratio_rows = payload["species"]["Ti"]["observations"]
    assert len(ratio_rows) == 10
    assert [row["values"]["value"] for row in ratio_rows] == [
        26.39, 29.46, 25.33, 19.97, 21.40,
        19.19, 19.14, 16.88, 12.57, 15.91,
    ]
    assumption = (
        "In experiments in air we assumed all Ti as Ti4+ "
        "(25.2wt% TiO2 at 1,300°C)."
    )
    for row in ratio_rows:
        assert row["values"]["quantity"] == "valence_ratio"
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["values"]["valence_basis"] == {
            "numerator_oxidation_states": [4],
            "denominator_kind": "oxidation_state",
            "denominator_oxidation_state": 3,
            "value_form": "ratio",
        }
        assert len(row["derived_from"]) == 2
        assert row["derivation"]["inputs"] == row["derived_from"]
        assert row["derivation"]["note"] == assumption
        assert row["values"]["T_C"] == 1300
        assert row["point_conditions"]["fO2_log"]["state"]["tag"] == "value"
    assert check_all_fidelity_samples_match(payload, label=EXTRACT.stem) == []
    mutated = copy.deepcopy(payload)
    mutated["species"]["Ti"]["observations"][0]["values"]["value"] += 0.01
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)
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
    assert len(migrated) == 41
    gamma = [row for row in migrated if row.identity.quantity.value.value == "activity_coefficient"]
    assert len(gamma) == 10
    first = next(row for row in gamma if "dat32n_activity_coefficient" in row.observation_id)
    assert str(first.value.point) == "6.63"
    assert str(first.point_conditions["XTiO2"].state.value) == "0.1508"
    assert str(first.point_conditions["TiO2_wt_pct"].state.value) == "19.09"
    assert str(first.point_conditions["temperature_K"].state.value) == "1495"
    assert first.identity.composition.reason == "no composition mapped from source"
    assert sum(row.identity.quantity.value.value == "activity" for row in migrated) == 1
    ratios = [
        row for row in migrated
        if row.identity.quantity.is_value
        and row.identity.quantity.value.value == "valence_ratio"
    ]
    assert len(ratios) == 10
    assert {row.value.point for row in ratios} == {
        Decimal(value)
        for value in (
            "26.39", "29.46", "25.33", "19.97", "21.40",
            "19.19", "19.14", "16.88", "12.57", "15.91",
        )
    }
    table_3_migrated = [
        row for row in migrated
        if "::borisov_2004_table_3_" in row.observation_id
    ]
    assert len(table_3_migrated) == 30
    assert all(
        row.point_conditions["temperature_K"].state.value == Decimal("1573.15")
        for row in table_3_migrated
    )
    assert all(
        row.point_conditions["fO2_log"].state.is_value
        and row.point_conditions["fO2_log"].locator.page == 4
        for row in table_3_migrated
    )
    assert not migrator.result.validation.hard_issues
