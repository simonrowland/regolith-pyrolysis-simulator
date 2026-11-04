"""Redox predictor inputs are projected from their printed source rows."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import NoticeKind
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction

REPO = Path(__file__).resolve().parents[2]
SOSS = REPO / "data/literature/extracts/sossi-2020-magma-ocean-redox.yaml"
AITHALA = REPO / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
KRESS = (
    REPO
    / "data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml"
)
AITHALA_ALLF_RUNS = {
    "aithala-2026-1800-30",
    "aithala-2026-1900-30",
    "aithala-2026-2000-10",
    "aithala-2026-2000-30",
    "aithala-2026-2000-120",
    "aithala-2026-2100-30",
}


def _migrate(path: Path):
    migrator = Migrator(root=REPO)
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _composition(observation):
    state = observation.identity.composition
    assert state is not None and state.is_value
    return state.value


def test_kress_table1_redox_rows_project_printed_conditions() -> None:
    extract = yaml.safe_load(KRESS.read_text(encoding="utf-8"))
    series = next(
        item
        for item in extract["species"]["Fe"]["observations"]
        if item["observation_id"] == "kress_1988_table1_measured_redox_series"
    )
    source_rows = series["values"]["rows"]
    assert len(source_rows) == 63
    printed_cells = {
        "71": {
            "T_C": "1461",
            "log10_fO2": "-7.17",
            "SiO2": "51.36",
            "Fe2O3": "1.09",
            "FeO": "10.34",
        },
        "B120": {
            "T_C": "1632",
            "log10_fO2": "-4.74",
            "SiO2": "52.02",
            "Fe2O3": "2.75",
            "FeO": "10.01",
        },
    }
    by_run = {str(row["experiment_id"]): row for row in source_rows}
    for run_id, expected in printed_cells.items():
        row = by_run[run_id]
        assert row["T_C"] == expected["T_C"]
        assert row["log10_fO2"] == expected["log10_fO2"]
        assert row["oxide_wt_percent"]["SiO2"] == expected["SiO2"]
        assert row["oxide_wt_percent"]["Fe2O3"] == expected["Fe2O3"]
        assert row["Fe2O3_wt_pct"] == expected["Fe2O3"]
        assert row["oxide_wt_percent"]["FeO"] == expected["FeO"]
        assert str(row["FeO_wt_pct"]) == expected["FeO"]

    result = _migrate(KRESS)
    redox_rows = [
        row
        for row in result.observations.values()
        if row.source_id == "kress-carmichael-1988-iron-oxidation-stoichiometry"
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(redox_rows) == 63
    observed_runs = set()
    for row in redox_rows:
        conditions = row.point_conditions or {}
        for key in ("temperature_K", "fO2_log", "composition"):
            condition = conditions[key]
            assert condition.state.is_value
            assert condition.locator is not None
            assert condition.locator.table == "1"
        run_id = conditions["fO2_log"].locator.record
        observed_runs.add(run_id)
        assert conditions["temperature_K"].locator.record == run_id
        assert conditions["composition"].locator.record == run_id
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        assert row.identity.composition is not None
        assert row.identity.composition.is_value
    assert observed_runs == set(by_run)


def test_sossi_redox_row_projects_its_printed_glass_analysis() -> None:
    result = _migrate(SOSS)
    observation = result.observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    printed = {
        "SiO2": Decimal("47.04"),
        "Al2O3": Decimal("4.34"),
        "FeOT": Decimal("8.38"),
        "MgO": Decimal("37.61"),
        "CaO": Decimal("2.02"),
        "Na2O": Decimal("0.01"),
        "K2O": Decimal("0.00"),
        "SO2": Decimal("0.00"),
        "Total": Decimal("99.39"),
    }
    expected = wt_pct_to_mole_fraction(printed)
    assert _composition(observation) == expected
    point = observation.point_conditions["composition"]
    assert point.state.is_value and point.state.value == expected
    f_o2 = observation.point_conditions["fO2_log"]
    assert f_o2.state.is_value and f_o2.state.value == Decimal("0.00")
    redox_rows = [
        row
        for row in result.observations.values()
        if row.source_id == "sossi-2020-magma-ocean-redox"
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(redox_rows) == 12
    assert all(_composition(row) is not None for row in redox_rows)
    assert all(
        row.identity.temperature_K is not None and row.identity.temperature_K.is_value
        for row in redox_rows
    )
    assert all(
        row.point_conditions["temperature_K"].state.is_value for row in redox_rows
    )
    printed_logs = {
        "sossi_2020_per_f_o2_1_xanes": Decimal("0.00"),
        "sossi_2020_per_f_o2_2_xanes": Decimal("-2.49"),
        "sossi_2020_per_f_o2_3_xanes": Decimal("-2.53"),
        "sossi_2020_per_f_o2_6_xanes": Decimal("-2.58"),
        "sossi_2020_per_ts1_xanes": Decimal("-2.72"),
        "sossi_2020_per_f_o2_7_xanes": Decimal("-2.72"),
        "sossi_2020_per_ts2_xanes": Decimal("-2.72"),
        "sossi_2020_per_f_o2_8_xanes": Decimal("-3.00"),
        "sossi_2020_per_f_o2_9_xanes": Decimal("-3.92"),
        "sossi_2020_per_f_o2_10_xanes": Decimal("-5.32"),
        "sossi_2020_per_f_o2_11_xanes": Decimal("-6.74"),
        "sossi_2020_per_f_o2_12_xanes": Decimal("-7.86"),
    }
    assert len(printed_logs) == len(redox_rows) == 12
    for row in redox_rows:
        row_id = row.observation_id.rsplit("::", 1)[-1]
        f_o2 = row.point_conditions["fO2_log"]
        assert f_o2.state.is_value
        assert f_o2.state.value == printed_logs[row_id]


def test_aithala_redox_row_projects_experiment_glass_analysis_and_f_o2() -> None:
    result = _migrate(AITHALA)
    observation = result.observations[
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes"
    ]
    printed = {
        "SiO2": Decimal("47.24"),
        "TiO2": Decimal("0.53"),
        "Al2O3": Decimal("11.32"),
        "FeOT": Decimal("16.04"),
        "MgO": Decimal("12.54"),
        "CaO": Decimal("7.88"),
        "Na2O": Decimal("1.98"),
        "K2O": Decimal("0.029"),
        "P2O5": Decimal("0.13"),
    }
    expected = wt_pct_to_mole_fraction(printed)
    assert _composition(observation) == expected
    point = observation.point_conditions["composition"]
    assert point.state.is_value and point.state.value == expected
    f_o2 = observation.point_conditions["fO2_log"]
    assert f_o2.state.is_value and f_o2.state.value == Decimal("0.0")
    redox_rows = [
        row
        for row in result.observations.values()
        if row.source_id == "aithala-2026-magmatic-iron-redox-to-2100c"
        and row.experiment_id.rsplit("::", 1)[-1] in AITHALA_ALLF_RUNS
        and row.observation_id.rsplit("::", 1)[-1].endswith("_xanes")
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(redox_rows) == 6
    assert all(_composition(row) is not None for row in redox_rows)
    assert {row.identity.temperature_K.value for row in redox_rows} == {
        Decimal(value) for value in ("2073.15", "2173.15", "2273.15", "2373.15")
    }
    assert all(
        row.point_conditions["temperature_K"].state.is_value for row in redox_rows
    )
    assert all("fO2_log" in (row.point_conditions or {}) for row in redox_rows)


def test_aithala_vf_redox_rows_project_printed_sample_compositions() -> None:
    result = _migrate(AITHALA)
    printed = {
        "aithala-2026-vf271": {
            "sample_id": "VF271",
            "T_K": "1523.15",
            "SiO2": "51.55",
            "TiO2": "0.50",
            "Al2O3": "11.65",
            "FeOT": "12.07",
            "MgO": "11.00",
            "CaO": "8.32",
            "Na2O": "3.07",
            "K2O": "0.05",
            "P2O5": "0.30",
        },
        "aithala-2026-vf267": {
            "sample_id": "VF267",
            "T_K": "1573.15",
            "SiO2": "50.79",
            "TiO2": "0.54",
            "Al2O3": "9.98",
            "FeOT": "14.26",
            "MgO": "11.66",
            "CaO": "6.85",
            "Na2O": "3.05",
            "K2O": "0.06",
            "P2O5": "0.27",
        },
        "aithala-2026-vf231": {
            "sample_id": "VF231",
            "T_K": "1638.15",
            "SiO2": "47.20",
            "TiO2": "0.49",
            "Al2O3": "10.68",
            "FeOT": "15.94",
            "MgO": "11.81",
            "CaO": "7.67",
            "Na2O": "3.28",
            "K2O": "0.04",
            "P2O5": "0.48",
        },
        "aithala-2026-vf268": {
            "sample_id": "VF268",
            "T_K": "1673.15",
            "SiO2": "47.99",
            "TiO2": "0.49",
            "Al2O3": "10.95",
            "FeOT": "14.94",
            "MgO": "12.40",
            "CaO": "7.67",
            "Na2O": "2.72",
            "K2O": "0.06",
            "P2O5": "0.27",
        },
        "aithala-2026-vf270": {
            "sample_id": "VF270",
            "T_K": "1723.15",
            "SiO2": "47.96",
            "TiO2": "0.52",
            "Al2O3": "11.04",
            "FeOT": "14.87",
            "MgO": "12.43",
            "CaO": "7.66",
            "Na2O": "3.00",
            "K2O": "0.05",
            "P2O5": "0.35",
        },
        "aithala-2026-vf274": {
            "sample_id": "VF274",
            "T_K": "1773.15",
            "SiO2": "47.92",
            "TiO2": "0.49",
            "Al2O3": "11.16",
            "FeOT": "15.04",
            "MgO": "12.48",
            "CaO": "7.76",
            "Na2O": "3.06",
            "K2O": "0.07",
            "P2O5": "0.29",
        },
    }
    extract = yaml.safe_load(AITHALA.read_text(encoding="utf-8"))
    source_rows = {
        item["observation_id"]: item
        for item in extract["species"]["Fe"]["observations"]
    }
    redox_rows = [
        row
        for row in result.observations.values()
        if row.source_id == "aithala-2026-magmatic-iron-redox-to-2100c"
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
        and row.experiment_id.rsplit("::", 1)[-1] in printed
    ]
    assert len(redox_rows) == 10
    assert {row.experiment_id.rsplit("::", 1)[-1] for row in redox_rows} == set(
        printed
    )
    for row in redox_rows:
        run_id = row.experiment_id.rsplit("::", 1)[-1]
        sample = printed[run_id]
        source = source_rows[row.observation_id.rsplit("::", 1)[-1]]
        assert source["values"]["sample_id"] == sample["sample_id"]
        expected = wt_pct_to_mole_fraction(
            {
                key: Decimal(value)
                for key, value in sample.items()
                if key not in {"sample_id", "T_K"}
            }
        )
        assert _composition(row) == expected
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(sample["T_K"])
        point = row.point_conditions
        assert point["temperature_K"].state.is_value
        assert point["temperature_K"].locator.table == "S-1"
        assert point["fO2_log"].state.is_value
        assert point["fO2_log"].state.value == Decimal("-0.68")
        assert point["fO2_log"].locator.table == "S-1"
        assert point["composition"].state.is_value
        assert point["composition"].state.value == expected
        assert point["composition"].locator.table == "S-2"


def test_aithala_vf_composition_join_uses_the_printed_sample_id(tmp_path: Path) -> None:
    extract = AITHALA.read_text(encoding="utf-8")
    extract = extract.replace(
        "        quantity: fe3_fe2_ratio\n        sample_id: VF271\n",
        "        quantity: fe3_fe2_ratio\n        sample_id: unmatched-run\n",
        1,
    )
    path = tmp_path / AITHALA.name
    path.write_text(extract, encoding="utf-8")

    result = _migrate(path)
    observation = result.observations[
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_vf271_xanes"
    ]
    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown


def test_aithala_row_printed_f_o2_overrides_experiment_value(tmp_path: Path) -> None:
    extract = AITHALA.read_text(encoding="utf-8")
    extract = extract.replace(
        "        quantity: fe3_fe2_ratio\n        sample_id: 1800-30\n",
        "        quantity: fe3_fe2_ratio\n        sample_id: 1800-30\n        log_fO2: -5.0\n",
        1,
    )
    path = tmp_path / AITHALA.name
    path.write_text(extract, encoding="utf-8")

    result = _migrate(path)
    observation = result.observations[
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes"
    ]
    f_o2 = observation.point_conditions["fO2_log"]
    assert f_o2.state.is_value and f_o2.state.value == Decimal("-5.0")


def test_aithala_rejected_row_f_o2_refuses_experiment_fallback(tmp_path: Path) -> None:
    extract = AITHALA.read_text(encoding="utf-8")
    extract = extract.replace(
        "        quantity: fe3_fe2_ratio\n        sample_id: 1800-30\n",
        "        quantity: fe3_fe2_ratio\n        sample_id: 1800-30\n        log_fO2: '< -5'\n",
        1,
    )
    path = tmp_path / AITHALA.name
    path.write_text(extract, encoding="utf-8")

    result = _migrate(path)
    observation = result.observations[
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes"
    ]
    f_o2 = observation.point_conditions["fO2_log"]
    assert f_o2.state.is_unknown
    assert "bounded or not absolute" in f_o2.state.reason


def test_feot_notice_follows_row_local_and_joined_compositions() -> None:
    aithala = _migrate(AITHALA)
    aithala_rows = [
        row
        for row in aithala.observations.values()
        if row.source_id == "aithala-2026-magmatic-iron-redox-to-2100c"
    ]
    feot_rows = [
        row
        for row in aithala_rows
        if (composition := (row.point_conditions or {}).get("composition"))
        is not None
        and composition.inference is not None
        and any(
            "FeOT (total iron) treated as FeO" in item
            for item in composition.inference.inputs
        )
    ]
    assert len(aithala_rows) == 28
    assert len(feot_rows) == 28
    projected_redox_rows = [
        row
        for row in feot_rows
        if row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(projected_redox_rows) == 16
    missing_aithala_notice = [
        row.observation_id
        for row in projected_redox_rows
        if not any(
            notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
            for notice in row.notices
        )
    ]
    assert not missing_aithala_notice

    sossi = _migrate(SOSS)
    sossi_rows = [
        row
        for row in sossi.observations.values()
        if row.source_id == "sossi-2020-magma-ocean-redox"
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(sossi_rows) == 12
    assert all(
        any(
            notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
            for notice in row.notices
        )
        for row in sossi_rows
    )
