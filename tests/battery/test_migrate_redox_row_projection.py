"""Redox predictor inputs are projected from their printed source rows."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction

REPO = Path(__file__).resolve().parents[2]
SOSS = REPO / "data/literature/extracts/sossi-2020-magma-ocean-redox.yaml"
AITHALA = REPO / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
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
    assert all("fO2_log" in (row.point_conditions or {}) for row in redox_rows)


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


def test_aithala_composition_join_uses_the_printed_sample_id(tmp_path: Path) -> None:
    extract = AITHALA.read_text(encoding="utf-8")
    extract = extract.replace(
        "        quantity: fe3_fe2_ratio\n        sample_id: 1800-30\n",
        "        quantity: fe3_fe2_ratio\n        sample_id: unmatched-run\n",
        1,
    )
    path = tmp_path / AITHALA.name
    path.write_text(extract, encoding="utf-8")

    result = _migrate(path)
    observation = result.observations[
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes"
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
