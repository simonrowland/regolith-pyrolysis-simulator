import math
from types import SimpleNamespace

import pytest

import probe_d452_r4_sizing as sizing_probe
from simulator.condensation import WallSaturationPressureRefusal
from probe_d452_r4_sizing import (
    _size_peak_to_capture_target,
    _snapshot_model_state,
)


def test_ceiling_limited_stage_uses_finite_area_and_reports_ceiling_flag():
    model = SimpleNamespace(
        stage_area_m2_by_stage={"cr_stage2": 0.05},
        wall_species_partial_pressures_pa={},
        vapor_pressure_data={},
        lab_geometry=None,
        gas_temperature_C=1400.0,
        overhead_pressure_mbar=1.0,
        stir_factor=1.0,
        radial_stir_factor=1.0,
        regime_factor=1.0,
        carrier_gas="N2",
        stage_hydraulic_diameter_m_by_stage={},
        pipe_diameter_m=0.5,
    )

    def ceiling_limited_resolver(**_kwargs):
        area = model.stage_area_m2_by_stage["cr_stage2"]
        return min(0.73, 0.1 * area)

    peak = {
        "stage_key": "cr_stage2",
        "current_area_m2": 0.05,
        "kwargs": {},
        "model_state": _snapshot_model_state(model),
    }
    sizing = _size_peak_to_capture_target(
        model,
        ceiling_limited_resolver,
        peak,
    )

    assert sizing["status"] == "capture_ceiling_below_target"
    assert math.isfinite(float(sizing["area_m2"]))
    assert 0.0 < float(sizing["area_m2"]) < 1.0e300
    assert sizing["ceiling_eta"] == pytest.approx(0.73)
    assert sizing["target_eta"] == pytest.approx(0.95 * 0.73)
    assert sizing["eta"] == pytest.approx(sizing["target_eta"])
    assert model.stage_area_m2_by_stage["cr_stage2"] == 0.05


def test_wall_psat_refusals_remain_typed_and_do_not_abort_probe(monkeypatch):
    refusal = WallSaturationPressureRefusal(
        "SiO", 1400.0, "wall fit outside its finite positive-denominator branch"
    )

    def refused_dewpoint(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(
        sizing_probe, "antoine_dew_temperature_diagnostic", refused_dewpoint
    )
    row = sizing_probe._dewpoint_in_wall_band(
        "SiO", 10.0, 1126.85, 1200.0, {}, {}
    )
    assert row["status"] == "psat_refused"
    assert "WallSaturationPressureRefusal" in row["reason"]

    def refused_psat(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(sizing_probe, "_try_antoine_psat_pa", refused_psat)
    psat, was_refused, _extrapolated, reason = sizing_probe._production_psat(
        "SiO", 1126.85, {}, {}
    )
    assert psat is None
    assert was_refused is True
    assert "WallSaturationPressureRefusal" in reason
