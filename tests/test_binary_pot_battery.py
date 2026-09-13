"""Laptop-runnable tests for the binary-pot engine arm. No melt engines."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    DEFAULT_POTS_PATH,
    QUANTITY_ACTIVITY,
    REFUSAL_TIMEOUT,
    REFUSAL_UNAVAILABLE,
    BinaryPot,
    EngineHandle,
    EquilibrateCell,
    Po2Request,
    classify_equilibrate_outcome,
    equilibrate_cell,
    load_binary_pots,
    pairwise_residuals,
    render_report_markdown,
    residual_log10,
    write_reports,
)


FIXTURE_REPORT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "binary_pot_battery"
    / "engine_arm_report.json"
)


def test_pots_load_and_sum_to_100_wt_pct() -> None:
    pots, grid = load_binary_pots(DEFAULT_POTS_PATH)
    assert len(pots) == 8
    ids = [pot.pot_id for pot in pots]
    assert ids == [
        "sio2_al2o3_50_50",
        "sio2_al2o3_80_20",
        "cao_sio2_40_60",
        "cao_al2o3_50_50",
        "mgo_sio2_40_60",
        "feo_sio2_40_60",
        "na2o_sio2_25_75",
        "feo_mgo_sio2_30_20_50",
    ]
    for pot in pots:
        total = sum(pot.composition_wt_pct.values())
        assert total == pytest.approx(100.0)
        assert len(pot.composition_wt_pct) >= 2
        assert pot.why
    assert grid.temperatures_K[0] == 1500.0
    assert grid.temperatures_K[-1] == 2300.0
    assert grid.temperatures_K == tuple(range(1500, 2301, 100))
    assert grid.engine_default_po2 is True
    assert grid.commanded_po2_bar == (1.0e-8,)
    kato = {
        pot.pot_id: pot.kato_1993_table4_system for pot in pots
    }
    assert kato["na2o_sio2_25_75"] is None
    assert kato["feo_mgo_sio2_30_20_50"] == "FeO-MgO-SiO2"
    assert kato["sio2_al2o3_50_50"] == "Al2O3-SiO2"


def test_refusing_engine_yields_typed_refusal_row_not_exception() -> None:
    pot = BinaryPot(
        pot_id="fixture_pot",
        kato_1993_table4_system="Al2O3-SiO2",
        why="fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )
    po2 = Po2Request(mode="engine_default", po2_bar=None)

    missing = EngineHandle(
        name="alphamelts",
        backend=None,
        available=False,
        unavailable_reason="backend unavailable",
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    missing_cell = equilibrate_cell(
        missing, pot, temperature_K=1500.0, po2=po2
    )
    assert missing_cell.status == "refusal"
    assert missing_cell.refusal_reason == REFUSAL_UNAVAILABLE
    assert missing_cell.melt_activities == {}

    class _BoomBackend:
        def equilibrate(self, **kwargs):
            raise RuntimeError("spin forever")

    exploding = EngineHandle(
        name="thermoengine",
        backend=_BoomBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    boom_cell = equilibrate_cell(
        exploding, pot, temperature_K=1600.0, po2=po2, timeout_s=1.0
    )
    assert boom_cell.status == "refusal"
    assert boom_cell.refusal_reason == REFUSAL_UNAVAILABLE
    assert boom_cell.engine_status == "RuntimeError"

    class _TimeoutBackend:
        def equilibrate(self, **kwargs):
            raise TimeoutError("equilibrate exceeded hard timeout of 1s")

    timed = EngineHandle(
        name="thermoengine",
        backend=_TimeoutBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    timeout_cell = equilibrate_cell(
        timed, pot, temperature_K=1700.0, po2=po2, timeout_s=1.0
    )
    assert timeout_cell.status == "refusal"
    assert timeout_cell.refusal_reason == REFUSAL_TIMEOUT


def test_classify_out_of_domain_and_no_liquid() -> None:
    status, reason, engine_reason = classify_equilibrate_outcome(
        SimpleNamespace(
            status="out_of_domain",
            diagnostics={"backend_status_reason": "major_sum"},
            warnings=["DomainGate rejected: major oxide sum <= 95 wt%"],
            activity_coefficients={},
            vapor_pressures_Pa={},
            liquid_fraction=None,
        )
    )
    assert status == "refusal"
    assert reason == "sum_below_95_wt_pct"
    assert engine_reason

    status, reason, _ = classify_equilibrate_outcome(
        SimpleNamespace(
            status="ok",
            diagnostics={},
            warnings=[],
            activity_coefficients={"SiO2": 0.4},
            vapor_pressures_Pa={},
            liquid_fraction=0.0,
            phase_assemblage_available=True,
        )
    )
    assert status == "refusal"
    assert reason == "no_liquid"


def test_residual_function_is_antisymmetric() -> None:
    assert residual_log10(10.0, 1.0) == pytest.approx(1.0)
    assert residual_log10(1.0, 10.0) == pytest.approx(-1.0)
    assert residual_log10(1.0, 1.0) == pytest.approx(0.0)
    left = residual_log10(3.0, 2.0)
    right = residual_log10(2.0, 3.0)
    assert left == pytest.approx(-right)
    with pytest.raises(Exception):
        residual_log10(0.0, 1.0)

    cells = [
        EquilibrateCell(
            pot_id="p",
            engine="alphamelts",
            temperature_K=1500.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={"SiO2": 10.0},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        ),
        EquilibrateCell(
            pot_id="p",
            engine="thermoengine",
            temperature_K=1500.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={"SiO2": 1.0},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        ),
    ]
    rows = pairwise_residuals(cells)
    assert len(rows) == 1
    assert rows[0]["quantity"] == QUANTITY_ACTIVITY
    assert rows[0]["engine_a"] == "alphamelts"
    assert rows[0]["engine_b"] == "thermoengine"
    assert rows[0]["delta_log10_a_minus_b"] == pytest.approx(1.0)
    swapped_cells = [
        EquilibrateCell(
            **{**cells[0].__dict__, "melt_activities": {"SiO2": 1.0}}
        ),
        EquilibrateCell(
            **{**cells[1].__dict__, "melt_activities": {"SiO2": 10.0}}
        ),
    ]
    swapped = pairwise_residuals(swapped_cells)
    # Pair order is alphabetical; swapping values flips the signed dex.
    assert swapped[0]["engine_a"] == "alphamelts"
    assert swapped[0]["delta_log10_a_minus_b"] == pytest.approx(
        -rows[0]["delta_log10_a_minus_b"]
    )


def test_report_renders_from_fixture(tmp_path: Path) -> None:
    report = json.loads(FIXTURE_REPORT.read_text(encoding="utf-8"))
    markdown = render_report_markdown(report)
    assert markdown.startswith("# Binary-pot battery — engine arm")
    assert "cannot pass or fail" in markdown
    assert "descriptive magnitude band only" in markdown
    assert "## Refusal matrix (pot × engine)" in markdown
    assert "## Per-pot residual tables" in markdown
    assert "## Twenty largest in-envelope residuals" in markdown
    assert "`sio2_al2o3_50_50`" in markdown
    assert "large_1_to_2_dex" in markdown
    json_path, md_path = write_reports(report, tmp_path)
    assert json_path.name == "binary-pot-engine-arm.json"
    assert md_path.read_text(encoding="utf-8") == markdown


def test_battery_engine_names_match_backends_py_surface() -> None:
    assert BATTERY_ENGINE_NAMES == (
        "internal-analytical",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
        "cached-real",
    )
