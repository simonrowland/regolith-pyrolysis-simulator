"""Hourly retry behavior for OpenIMCC melt-activity refusals."""

from types import SimpleNamespace

import pytest

openimcc = pytest.importorskip("openimcc")
ImccNonconvergenceError = openimcc.ImccNonconvergenceError

import simulator.melt_backend.openimcc_bridge as openimcc_bridge_module
from simulator.runner import PyrolysisRun


def test_openimcc_is_retried_after_a_single_hour_refusal(monkeypatch) -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=2,
        mass_kg=1.0,
        backend_name="internal-analytical",
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    assert sim._high_t_melt_activity == "openimcc"
    sim.melt.temperature_C = 1800.0
    result = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        liquid_fraction=1.0,
        fO2_log=sim.melt.fO2_log,
        activity_coefficients={},
        diagnostics={},
    )
    real_evaluate_cleaned_melt = openimcc_bridge_module.evaluate_cleaned_melt
    target_temperatures: list[float] = []

    def refuse_first_hour(**kwargs):
        temperature_K = float(kwargs["temperature_K"])
        if temperature_K in (2073.15, 2080.65):
            target_temperatures.append(temperature_K)
        if len(target_temperatures) == 1 and temperature_K == 2073.15:
            raise ImccNonconvergenceError("forced first-hour refusal", {})
        return real_evaluate_cleaned_melt(**kwargs)

    monkeypatch.setattr(
        openimcc_bridge_module,
        "evaluate_cleaned_melt",
        refuse_first_hour,
    )

    sim._refresh_vapor_pressures_from_kernel(
        result, simulation_hour=int(sim.melt.hour) + 1,
    )
    first_hour = sim._last_high_t_melt_activity
    assert first_hour["provider"] == "constant_gamma"
    assert first_hour["fallback"] is True
    assert first_hour["fallback_reason"]["code"] == "imcc_nonconvergence"

    sim.melt.temperature_C += 7.5
    sim._refresh_vapor_pressures_from_kernel(
        result, simulation_hour=int(sim.melt.hour) + 2,
    )
    second_hour = sim._last_high_t_melt_activity
    assert target_temperatures == [2073.15, 2080.65]
    assert second_hour["provider"] == "openimcc"
    assert second_hour["fallback"] is False
    assert "fallback_reason" not in second_hour
    assert [
        step["hour"] for step in sim._high_t_melt_activity_fallback_steps
    ] == [1]
