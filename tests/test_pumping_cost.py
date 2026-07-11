"""Unit tests for the rough sub-ambient pumping-cost helper (#52 KNOB-COST-PRESSURE)."""

import math
from types import SimpleNamespace

import pytest

from simulator.environment import MARS_DATUM_PRESSURE_BAR, MARS_OLYMPUS_PRESSURE_BAR
from simulator.pumping_cost import (
    MARS_DATUM_AMBIENT_PA,
    MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    MOON_AMBIENT_PA,
    estimate_subambient_pump_cost,
    pumping_context_from_sim,
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
    assert five_mbar.mean_power_W == pytest.approx(7.290887521956941 / 0.90)
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
            validated_line_conductance_m3_s=1000.0,
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
        validated_line_conductance_m3_s=1000.0,
    )
    assert deep.feasible is False


def test_compression_work_matches_intercooled_staged_adiabatic_formula():
    # Two equal-ratio stages are required when P2/P1=6.1 and each stage is
    # capped at 4:1. Perfect intercooling returns each stage inlet to 300 K.
    # W/mol = N * gamma/(gamma-1) * R*T
    #         * (r_stage**((gamma-1)/gamma) - 1) / eta_stage / eta_motor_drive.
    ndot, T, eff, motor_drive_eff = 0.01, 300.0, 0.70, 0.90
    tp, amb = 100.0, MARS_DATUM_AMBIENT_PA
    r = estimate_subambient_pump_cost(
        target_pressure_pa=tp,
        offgas_mol_per_s=ndot,
        duration_s=3600,
        ambient_pressure_pa=amb,
        gas_temperature_K=T,
        stage_isentropic_efficiency=eff,
        motor_drive_efficiency=motor_drive_eff,
    )
    assert r.compression_model == "intercooled-staged-adiabatic"
    assert r.compression_stages == 2
    assert r.stage_pressure_ratio == pytest.approx(math.sqrt(6.1))
    assert r.mean_power_W == pytest.approx(73.5236524284681 / motor_drive_eff)
    assert r.energy_kWh == pytest.approx(0.0735236524284681 / motor_drive_eff)
    assert r.required_pump_speed_m3_s == pytest.approx(0.24943387854)


def test_pump_feasibility_requires_regime_validated_line_conductance():
    unresolved = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
        max_pump_speed_m3_s=1.0,
    )
    assert unresolved.feasible is None
    assert unresolved.status == "missing-validated-line-conductance"

    feasible = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
        max_pump_speed_m3_s=1.0,
        validated_line_conductance_m3_s=1.0,
    )
    assert feasible.effective_speed_ceiling_m3_s == pytest.approx(0.5)
    assert feasible.required_pump_speed_m3_s == pytest.approx(0.24943387854)
    assert feasible.feasible is True

    infeasible = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
        max_pump_speed_m3_s=0.3,
        validated_line_conductance_m3_s=1.0,
    )
    assert infeasible.effective_speed_ceiling_m3_s == pytest.approx(0.3 / 1.3)
    assert infeasible.feasible is False
    assert infeasible.status == "pump-speed-limit-exceeded"


def test_finite_near_vacuum_target_fails_soft_without_overflow():
    result = estimate_subambient_pump_cost(
        target_pressure_pa=1e-308,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
    )

    assert result.regime == "pump"
    assert math.isfinite(result.energy_kWh)
    assert math.isinf(result.compression_ratio)
    assert math.isinf(result.required_pump_speed_m3_s)
    assert result.feasible is None
    assert result.status == "missing-validated-line-conductance"


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

    stage_efficiency = metadata["pump_stage_isentropic_efficiency"]
    assert stage_efficiency["status"] == "owner-ratify-placeholder"
    assert stage_efficiency["ticket"]
    assert "reciprocating-stage-efficiency" in stage_efficiency["source_tag"]
    assert "owner ratifies" in stage_efficiency["ratification_note"]
    motor_drive_efficiency = metadata["pump_motor_drive_efficiency"]
    assert motor_drive_efficiency["value"] == pytest.approx(0.90)
    assert "DOE-AMO-2014" in motor_drive_efficiency["source_tag"]
    assert "0.95 * 0.95 = 0.90" in motor_drive_efficiency["ratification_note"]
    stage_ratio = metadata["max_stage_pressure_ratio"]
    assert stage_ratio["value"] == pytest.approx(4.0)
    assert "DOE-QER-2015" in stage_ratio["source_tag"]
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


@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    (
        (
            {"stage_isentropic_efficiency": 1.01},
            "invalid-stage-isentropic-efficiency",
        ),
        (
            {"motor_drive_efficiency": 0.0},
            "invalid-motor-drive-efficiency",
        ),
        (
            {"max_stage_pressure_ratio": 1.0},
            "invalid-max-stage-pressure-ratio",
        ),
        (
            {"validated_line_conductance_m3_s": 0.0},
            "invalid-line-conductance",
        ),
    ),
)
def test_invalid_compressor_model_parameters_refuse(kwargs, expected_status):
    result = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
        **kwargs,
    )

    assert result.status == expected_status
    assert result.feasible is False


def test_pumping_context_accepts_explicit_ambient_without_body_metadata():
    context = pumping_context_from_sim(
        SimpleNamespace(melt=SimpleNamespace(ambient_pressure_mbar=6.1)),
        (),
    )

    assert context["status"] == "ok"
    assert context["ambient_pressure_pa"] == pytest.approx(610.0)
    assert context["rows"] == ()


def test_pumping_context_refuses_missing_ambient_pressure_instead_of_body_default():
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars"),
            # Production exposes this method, but it derives a body default.
            # Missing explicit pressure must still refuse.
            _vacuum_floor_bar=lambda: MARS_DATUM_AMBIENT_PA / 100_000.0,
        ),
        (),
    )

    assert context["status"] == "refused"
    assert context["reason"] == "missing-ambient-pressure"
    assert context["rows"] == ()


def test_pumping_context_refuses_missing_target_pressure():
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (
            SimpleNamespace(
                hour=7,
                temperature_C=500.0,
                overhead=SimpleNamespace(headspace_temperature_K=773.15),
                O2_vented_mol_hr=2.0,
                melt_offgas_O2_mol_hr=10.0,
                mre_anode_O2_mol_hr=4.0,
            ),
        ),
    )

    assert context["status"] == "refused"
    assert context["reason"] == "missing-target-pressure"
    assert context["hour"] == 7
    assert context["rows"] == ()


def test_pumping_context_skips_missing_target_when_vented_flow_is_zero():
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (
            SimpleNamespace(
                hour=8,
                overhead=SimpleNamespace(headspace_temperature_K=773.15),
                O2_vented_mol_hr=0.0,
            ),
        ),
    )

    assert context["status"] == "ok"
    assert context["rows"] == ()


def test_pumping_context_refuses_negative_vented_flow_before_target_pressure():
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (
            SimpleNamespace(
                hour=9,
                overhead=SimpleNamespace(headspace_temperature_K=773.15),
                O2_vented_mol_hr=-1.0,
            ),
        ),
    )

    assert context["status"] == "refused"
    assert context["reason"] == "invalid-o2-vented-flow"
    assert context["hour"] == 9


def test_pumping_context_only_costs_o2_not_already_compressed_by_turbine():
    snapshot = SimpleNamespace(
        hour=1,
        temperature_C=300.0,
        overhead=SimpleNamespace(
            pressure_mbar=1.0,
            headspace_temperature_K=300.0,
        ),
        # Melt/offgas O2 takes the turbine path; MRE-anode O2 is credited
        # directly to its terminal store. Only vented melt/offgas O2 bypasses
        # turbine compression and belongs in the Mars back-pressure sidecar.
        melt_offgas_O2_mol_hr=10.0,
        mre_anode_O2_mol_hr=4.0,
        O2_vented_mol_hr=2.0,
    )

    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (snapshot,),
    )

    assert context["status"] == "ok"
    assert context["energy_accounting_policy"] == (
        "uncompressed_o2_only; turbine-compressed_o2_is_already_charged"
    )
    assert len(context["rows"]) == 1
    assert context["rows"][0]["offgas_mol_per_s"] == pytest.approx(2.0 / 3600.0)

    snapshot.O2_vented_mol_hr = 0.0
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (snapshot,),
    )
    assert context["rows"] == ()
