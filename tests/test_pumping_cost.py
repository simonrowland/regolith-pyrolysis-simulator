"""Unit tests for the rough sub-ambient pumping-cost helper (#52 KNOB-COST-PRESSURE)."""

import math

import pytest

from simulator.environment import MARS_DATUM_PRESSURE_BAR, MARS_OLYMPUS_PRESSURE_BAR
from simulator.pumping_cost import (
    MARS_DATUM_AMBIENT_PA,
    MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    MOON_AMBIENT_PA,
    estimate_subambient_pump_cost,
    pumping_cost_parameters,
)


def test_moon_vent_free_zero_cost():
    # Any useful process pressure sits far above the lunar exosphere -> vent-free.
    r = estimate_subambient_pump_cost(
        target_pressure_pa=1e-4,  # 1e-9 bar
        offgas_mol_per_s=0.1,
        duration_s=7 * 3600,
        ambient_pressure_pa=MOON_AMBIENT_PA,
    )
    assert r.regime == "vent-free"
    assert r.energy_kWh == 0.0
    assert r.feasible is True


def test_mars_at_or_above_ambient_is_free():
    # Holding AT/above Mars ambient needs no sub-ambient pumping.
    r = estimate_subambient_pump_cost(
        target_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,  # exactly ambient
        offgas_mol_per_s=0.05,
        duration_s=3600,
        ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    )
    assert r.regime == "vent-free"
    assert r.energy_kWh == 0.0


def test_mars_pressure_constants_reuse_environment_source():
    assert MARS_DATUM_AMBIENT_PA == pytest.approx(MARS_DATUM_PRESSURE_BAR * 100_000.0)
    assert MARS_DATUM_AMBIENT_PA == pytest.approx(610.0)
    assert MARS_OLYMPUS_SUMMIT_AMBIENT_PA == pytest.approx(
        MARS_OLYMPUS_PRESSURE_BAR * 100_000.0
    )
    assert MARS_OLYMPUS_SUMMIT_AMBIENT_PA == pytest.approx(72.0)


def test_mars_5_to_15_mbar_band_straddles_datum_anchor():
    five_mbar = estimate_subambient_pump_cost(
        target_pressure_pa=500.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
    )
    fifteen_mbar = estimate_subambient_pump_cost(
        target_pressure_pa=1500.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
    )

    assert five_mbar.regime == "pump"
    assert five_mbar.compression_ratio == pytest.approx(1.22)
    assert five_mbar.mean_power_W == pytest.approx(33.066760631877486)
    assert fifteen_mbar.regime == "vent-free"
    assert fifteen_mbar.energy_kWh == 0.0


def test_mars_subambient_costs_energy_and_is_positive():
    r = estimate_subambient_pump_cost(
        target_pressure_pa=1e-2,  # 1e-7 bar, well below Olympus ambient
        offgas_mol_per_s=0.02,
        duration_s=7 * 3600,
        ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
        gas_temperature_K=1500.0,
    )
    assert r.regime == "pump"
    assert r.energy_kWh > 0.0
    assert r.compression_ratio > 1.0
    assert r.required_pump_speed_m3_s > 0.0


def test_deeper_vacuum_costs_more_and_eventually_infeasible():
    # Monotonic: lower target pressure -> more energy, larger pump speed.
    kw = []
    speeds = []
    for tp in (1e0, 1e-1, 1e-2, 1e-4):  # Pa, all below 72 Pa ambient
        r = estimate_subambient_pump_cost(
            target_pressure_pa=tp,
            offgas_mol_per_s=0.02,
            duration_s=7 * 3600,
            ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
            gas_temperature_K=1500.0,
        )
        kw.append(r.energy_kWh)
        speeds.append(r.required_pump_speed_m3_s)
    assert kw == sorted(kw)  # energy rises as target falls
    assert speeds == sorted(speeds)  # pump speed rises as target falls
    # The deepest target is infeasible (pump-size wall exceeds default ceiling).
    deep = estimate_subambient_pump_cost(
        target_pressure_pa=1e-4,
        offgas_mol_per_s=0.02,
        duration_s=7 * 3600,
        ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
        gas_temperature_K=1500.0,
    )
    assert deep.feasible is False


def test_compression_work_matches_isothermal_formula():
    # Hand computation:
    # 0.01 mol/s * 8.314462618 J/mol/K * 300 K * ln(610/100) / 0.15
    # = 300.6989878103832 W, for one hour = 0.3006989878103832 kWh.
    ndot, T, eff = 0.01, 300.0, 0.15
    tp, amb = 100.0, MARS_DATUM_AMBIENT_PA
    r = estimate_subambient_pump_cost(
        target_pressure_pa=tp,
        offgas_mol_per_s=ndot,
        duration_s=3600,
        ambient_pressure_pa=amb,
        gas_temperature_K=T,
        pump_isothermal_efficiency=eff,
    )
    assert r.mean_power_W == pytest.approx(300.6989878103832)
    assert r.energy_kWh == pytest.approx(0.3006989878103832)
    assert r.required_pump_speed_m3_s == pytest.approx(0.24943387854)


def test_perfect_vacuum_target_is_fail_soft_infeasible():
    r = estimate_subambient_pump_cost(
        target_pressure_pa=0.0,
        offgas_mol_per_s=0.1,
        duration_s=3600,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
    )

    assert r.regime == "invalid-target-pressure"
    assert r.energy_kWh == 0.0
    assert r.feasible is False
    assert math.isinf(r.required_pump_speed_m3_s)
    assert math.isinf(r.compression_ratio)


def test_zero_flow_with_zero_target_pressure_is_vent_free():
    r = estimate_subambient_pump_cost(
        target_pressure_pa=0.0,
        offgas_mol_per_s=0.0,
        duration_s=3600,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
    )

    assert r.regime == "vent-free"
    assert r.energy_kWh == 0.0
    assert r.feasible is True


def test_pump_placeholder_parameters_are_explicit_owner_ratify_metadata():
    metadata = {p.name: p.to_json() for p in pumping_cost_parameters()}

    assert metadata["pump_isothermal_efficiency"]["status"] == "owner-ratify-placeholder"
    assert metadata["pump_isothermal_efficiency"]["ticket"]
    assert "0.05_to_0.25" in metadata["pump_isothermal_efficiency"]["source_tag"]
    assert metadata["max_pump_speed_m3_s"]["status"] == "owner-ratify-placeholder"
    assert metadata["max_pump_speed_m3_s"]["ticket"]
    assert "real-pump-curve-datasheet-pinning-still-open" in metadata[
        "max_pump_speed_m3_s"
    ]["source_tag"]


def test_degenerate_inputs_fail_soft():
    # Non-finite / non-positive inputs must not raise (optimizer probe safety).
    for bad in (
        dict(target_pressure_pa=1.0, offgas_mol_per_s=0.0, duration_s=3600),
    ):
        r = estimate_subambient_pump_cost(ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA, **bad)
        assert r.regime == "vent-free"
        assert r.energy_kWh == 0.0
        assert r.feasible is True


def test_degenerate_pressure_inputs_fail_soft_infeasible():
    for bad_pressure in (float("nan"), -1.0):
        r = estimate_subambient_pump_cost(
            target_pressure_pa=bad_pressure,
            offgas_mol_per_s=0.1,
            duration_s=3600,
            ambient_pressure_pa=MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
        )
        assert r.regime == "invalid-target-pressure"
        assert r.energy_kWh == 0.0
        assert r.feasible is False
