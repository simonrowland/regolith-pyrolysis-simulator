"""Pins printed experimental and melt-activity tables for Borisov et al. (2004)."""

from pathlib import Path
import copy
from decimal import Decimal

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-2004-karrooite-liquidus-redox.yaml"
EXPECTED_RATIO_POINTS = {
    "DAT32-6": ("26.39", 1300, "9.84", "0.00001464591847708311051464573676"),
    "DAT32-18": ("29.46", 1300, "10.11", "0.000007865323909240218993677925160"),
    "DAT32-18a": ("25.33", 1300, "10.11", "0.000007865323909240218993677925160"),
    "DAT32-16": ("19.97", 1300, "10.28", "0.000005317611590980820842793892931"),
    "DAT32-7": ("21.40", 1300, "10.44", "0.000003678888389958051852550638810"),
    "DAT32-17": ("19.19", 1300, "10.75", "0.000001801841612221938528341658026"),
    "DAT32-17a": ("19.14", 1300, "10.75", "0.000001801841612221938528341658026"),
    "DAT32-17b": ("16.88", 1300, "10.75", "0.000001801841612221938528341658026"),
    "DAT32-11": ("12.57", 1301, "10.99", "0.000001036851624428474123201578321"),
    "DAT32-5": ("15.91", 1298, "11.12", "7.686287278983204536897541561E-7"),
}


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
    assumption = (
        "In experiments in air we assumed all Ti as Ti4+ "
        "(25.2wt% TiO2 at 1,300°C)."
    )
    ratios_by_sample = {row["locator"]["row"]: row for row in ratio_rows}
    assert set(ratios_by_sample) == set(EXPECTED_RATIO_POINTS)
    for sample, (expected_value, expected_temperature, minus_log_fO2, _fO2_pa) in (
        EXPECTED_RATIO_POINTS.items()
    ):
        row = ratios_by_sample[sample]
        assert row["locator"]["page"] == 4
        assert row["locator"]["pdf_page_index"] == 3
        assert len(row["values"]["points"]) == 1
        point = row["values"]["points"][0]
        assert point["sample"] == sample
        assert point["value"] == float(expected_value)
        assert point["as_published"] == expected_value
        assert point["T_C"] == expected_temperature
        assert point["log_fO2"]["value"] == -float(minus_log_fO2)
        assert point["log_fO2"]["units"] == "log10(atm)"
        assert point["log_fO2"]["locator"]["page"] == 4
        assert point["log_fO2"]["locator"]["pdf_page_index"] == 3
        assert point["log_fO2"]["locator"]["row"] == sample
        assert point["log_fO2"]["locator"]["column"] == "−log fO2"
        pressure = point["point_conditions"]["total_pressure_Pa"]
        assert pressure["state"] == {"tag": "value", "value": 101325}
        assert pressure["locator"]["page"] == 1
        assert pressure["locator"]["pdf_page_index"] == 0
        assert point.get("fO2_note") == ("calculated" if sample == "DAT32-5" else None)
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
    excess_rows = [
        row for row in table_3_tio2
        if row["values"]["basis_as_published"] == "TiO2 excess wt% as printed"
    ]
    assert len(excess_rows) == 10
    assert all("25.23 wt%" in row["derivation"]["relation"] for row in excess_rows)
    assert check_all_fidelity_samples_match(payload, label=EXTRACT.stem) == []
    mutated = copy.deepcopy(payload)
    mutated["species"]["Ti"]["observations"][0]["values"]["points"][0]["value"] += 0.01
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
    ratios_by_value = {row.value.point: row for row in ratios}
    assert set(ratios_by_value) == {
        Decimal(expected[0]) for expected in EXPECTED_RATIO_POINTS.values()
    }
    for sample, (expected_value, expected_temperature, minus_log_fO2, fO2_pa) in (
        EXPECTED_RATIO_POINTS.items()
    ):
        row = ratios_by_value[Decimal(expected_value)]
        expected_temperature_K = Decimal(expected_temperature) + Decimal("273.15")
        assert row.value.point == Decimal(expected_value)
        assert row.identity.temperature_K.value == expected_temperature_K
        assert row.identity.fO2_Pa.value == Decimal(fO2_pa)
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.composition.is_unknown
        assert row.point_conditions["temperature_K"].state.value == expected_temperature_K
        assert row.point_conditions["fO2_log"].state.value == Decimal(
            str(-float(minus_log_fO2))
        )
        assert row.point_conditions["fO2_log"].locator.page == 4
        total_pressure = row.point_conditions["total_pressure_Pa"]
        assert total_pressure.state.value == Decimal("101325")
        assert total_pressure.locator.page == 1
    table_3_migrated = [
        row for row in migrated
        if "::borisov_2004_table_3_" in row.observation_id
    ]
    assert len(table_3_migrated) == 30
    for row in table_3_migrated:
        if row.identity.quantity.is_value and row.identity.quantity.value.value == "valence_ratio":
            sample = next(
                sample
                for sample, expected in EXPECTED_RATIO_POINTS.items()
                if Decimal(expected[0]) == row.value.point
            )
            expected_temperature = EXPECTED_RATIO_POINTS[sample][1]
        else:
            expected_temperature = 1300
        assert row.point_conditions["temperature_K"].state.value == (
            Decimal(expected_temperature) + Decimal("273.15")
        )
    assert all(
        row.point_conditions["fO2_log"].state.is_value
        and row.point_conditions["fO2_log"].locator.page == 4
        for row in table_3_migrated
    )
    assert not migrator.result.validation.hard_issues
