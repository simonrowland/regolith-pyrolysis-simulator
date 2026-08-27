"""Unit tests for the rough sub-ambient pumping-cost helper (#52 KNOB-COST-PRESSURE)."""

import math
from types import SimpleNamespace

import pytest

from simulator.config import load_config_bundle
from simulator.cost_ledger import run_pumping_input_cost
from simulator.environment import MARS_DATUM_PRESSURE_BAR, MARS_OLYMPUS_PRESSURE_BAR
from simulator.pumping_cost import (
    ASTEROID_AMBIENT_PA,
    MARS_DATUM_AMBIENT_PA,
    MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    MOON_AMBIENT_PA,
    estimate_subambient_pump_cost,
    pumping_context_from_sim,
    pumping_cost_parameters,
    pumping_environment_for_feedstock,
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


@pytest.mark.parametrize(
    ("feedstock_id", "body", "ambient_pressure_pa"),
    (
        ("lunar_mare_low_ti", "moon", MOON_AMBIENT_PA),
        ("s_type_asteroid_silicate", "asteroid", ASTEROID_AMBIENT_PA),
        ("mars_global_mgs1", "mars", MARS_DATUM_AMBIENT_PA),
    ),
)
def test_pumping_feedstock_map_resolves_body_and_ambient(
    feedstock_id,
    body,
    ambient_pressure_pa,
):
    environment = pumping_environment_for_feedstock(feedstock_id)
    assert environment["status"] == "ok"
    assert environment["body"] == body
    assert environment["ambient_pressure_pa"] == pytest.approx(ambient_pressure_pa)

    context = pumping_context_from_sim(
        SimpleNamespace(
            record=SimpleNamespace(feedstock_key=feedstock_id),
            # The pumping-only feedstock map is authoritative; it does not
            # mutate the chemistry environment on melt.
            melt=SimpleNamespace(body="", ambient_pressure_mbar=0.0),
        ),
        (),
    )
    assert context["status"] == "ok"
    assert context["body"] == body
    assert context["ambient_pressure_pa"] == pytest.approx(ambient_pressure_pa)
    assert context["ambient_pressure_source"].startswith("pumping-feedstock-map-v1:")


def test_pumping_feedstock_map_refuses_unknown_feedstock():
    context = pumping_context_from_sim(
        SimpleNamespace(
            record=SimpleNamespace(feedstock_key="unmapped_future_feedstock"),
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (),
    )
    assert context["status"] == "refused"
    assert context["reason"] == "unsupported-feedstock"
    assert context["feedstock_id"] == "unmapped_future_feedstock"


def test_pumping_feedstock_map_covers_every_configured_feedstock():
    feedstocks = load_config_bundle().feedstocks
    refused = [
        feedstock_id
        for feedstock_id in feedstocks
        if pumping_environment_for_feedstock(feedstock_id)["status"] != "ok"
    ]
    assert refused == []


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
    # b-288 renamed this regime and NOTHING ELSE about this case. Every
    # behavioural assertion below is unchanged and still passes: fail-soft,
    # zero energy, infinite pump speed, infinite compression ratio.
    #
    # Those two infinities are the argument for the rename. The code was ALREADY
    # computing this as a physical divergence -- log(ambient/target) -> inf as
    # target -> 0 -- and then labelling the result "invalid-target-pressure",
    # which tells an operator their NUMBER was malformed when in fact their GOAL
    # was impossible. Holding an absolute vacuum against a real atmosphere is a
    # coherent request with an infinite answer, not a typo.
    #
    # The old label also collided with the genuinely-malformed cases (NaN,
    # negative), which still return "invalid-target-pressure" and are pinned by
    # test_degenerate_pressure_inputs_fail_soft_infeasible. One label for both
    # meant the two could not be told apart downstream.
    r = estimate_subambient_pump_cost(
        target_pressure_pa=0.0,
        offgas_mol_per_s=0.1,
        duration_s=3600,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
    )

    assert r.regime == "unreachable-absolute-vacuum-target"
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


def test_pumping_context_refuses_a_snapshot_missing_the_vented_flow_field():
    """An ABSENT field is not a zero flow.

    The sibling above covers an invalid VALUE. This covers a missing FIELD,
    which used to default to 0.0 and was therefore indistinguishable from a
    genuine zero: the row was skipped silently and the recipe's pumping load --
    a number the optimizer ranks by -- came out understated. The old code was
    careful about a bad value and careless about a missing field.

    The reason is asserted BY NAME on purpose. Checking only that the status is
    not ok would also pass on missing-ambient-pressure and
    missing-target-pressure, which are different honest refusals reachable from
    this same call, so the test would survive the defect it names.
    """
    context = pumping_context_from_sim(
        SimpleNamespace(
            melt=SimpleNamespace(body="mars", ambient_pressure_mbar=6.1),
        ),
        (
            SimpleNamespace(
                hour=9,
                overhead=SimpleNamespace(headspace_temperature_K=773.15),
                # O2_vented_mol_hr deliberately absent -- a carrier that is not
                # a real HourSnapshot, which declares it as a typed float.
            ),
        ),
    )

    assert context["status"] == "refused"
    assert context["reason"] == "missing-o2-vented-flow"


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


def test_pumping_context_forwards_only_explicit_validated_line_conductance():
    snapshot = SimpleNamespace(
        hour=1,
        temperature_C=300.0,
        overhead=SimpleNamespace(
            pressure_mbar=1.0,
            headspace_temperature_K=300.0,
            validated_line_conductance_m3_s=1000.0,
            pipe_conductance_kg_hr=50.0,
        ),
        O2_vented_mol_hr=2.0,
    )
    context = pumping_context_from_sim(
        SimpleNamespace(
            record=SimpleNamespace(feedstock_key="mars_global_mgs1"),
            melt=SimpleNamespace(body="", ambient_pressure_mbar=0.0),
        ),
        (snapshot,),
    )

    assert context["ambient_pressure_pa"] == pytest.approx(MARS_DATUM_AMBIENT_PA)
    assert context["rows"][0]["validated_line_conductance_m3_s"] == 1000.0
    _, diagnostic = run_pumping_input_cost(context)
    assert diagnostic["status"] == "ok"
    assert diagnostic["feasible"] is True
    assert diagnostic["pumping_electrical_kWh"] > 0.0


# --- b-259: a missing offgas rate is not a proven zero -----------------------
# The degenerate branch used to answer two of the three fail-closed categories
# with one return, so a NaN offgas rate came back as a free vent, indistinguishable
# from a genuine zero -- on a number the optimizer ranks candidates by.

_B259_BASE = dict(ambient_pressure_pa=101325.0, gas_temperature_K=500.0)


@pytest.mark.parametrize(
    "offgas,duration,token",
    [
        (math.nan, 3600.0, "invalid-offgas-rate"),
        (math.inf, 3600.0, "invalid-offgas-rate"),
        (1.0, math.nan, "invalid-duration"),
        (1.0, math.inf, "invalid-duration"),
    ],
)
def test_missing_offgas_or_duration_refuses_instead_of_venting_free(offgas, duration, token):
    """A rate we do not know is not a rate of zero.

    cost_ledger.py passes _finite(row.get("offgas_mol_per_s"), math.nan), so an
    absent ledger key reaches this function as NaN. Answering that with a free
    vent understates the recipe's cost, and cost is a number the optimizer ranks
    candidates by.
    """
    result = estimate_subambient_pump_cost(
        target_pressure_pa=100.0, offgas_mol_per_s=offgas, duration_s=duration, **_B259_BASE
    )
    assert result.status == token
    assert result.feasible is False
    # Pin the regime too. A NOT-FIXED review observed that without this, a
    # reintroduction returning regime="vent-free" alongside the refusing status
    # would still pass -- the assertion would be blind to a result that still
    # calls itself a free vent while claiming to refuse.
    assert result.regime == token


@pytest.mark.parametrize("offgas,duration", [(0.0, 3600.0), (1.0, 0.0)])
def test_genuine_zero_offgas_keeps_its_free_vent(offgas, duration):
    """A proven zero keeps the zero -- nothing to pump really is free.

    EXACT zero only. A first version of this parametrization included
    offgas=-1.0 here and asserted status="ok"/feasible=True for it, which
    ratified a negative molar flow as a proven zero. A review caught it and
    pointed at the contradiction: the snapshot-boundary test above already
    requires the same negative flow to refuse as invalid-o2-vented-flow, so
    the two tests disagreed about the same number.
    """
    result = estimate_subambient_pump_cost(
        target_pressure_pa=100.0, offgas_mol_per_s=offgas, duration_s=duration, **_B259_BASE
    )
    assert result.regime == "vent-free"
    assert result.status == "ok"
    assert result.feasible is True
    assert result.energy_kWh == 0.0


@pytest.mark.parametrize(
    "offgas,duration,token",
    [
        (-1.0, 3600.0, "invalid-offgas-rate"),
        (-1e-12, 3600.0, "invalid-offgas-rate"),
        (1.0, -1.0, "invalid-duration"),
    ],
)
def test_negative_inputs_refuse_rather_than_venting_free(offgas, duration, token):
    """A negative rate is an impossible measurement, not a small one.

    Distinct from the missing-input cases above: those are values we do not
    have, these are values we have and that cannot be real. Both refuse, and
    neither may borrow the proven zero's free vent.
    """
    result = estimate_subambient_pump_cost(
        target_pressure_pa=100.0, offgas_mol_per_s=offgas, duration_s=duration, **_B259_BASE
    )
    assert result.status == token
    assert result.regime == token
    assert result.feasible is False


def test_missing_input_is_distinguishable_from_a_proven_zero():
    """The defect was that these two were byte-identical results."""
    missing = estimate_subambient_pump_cost(
        target_pressure_pa=100.0, offgas_mol_per_s=math.nan, duration_s=3600.0, **_B259_BASE
    )
    proven_zero = estimate_subambient_pump_cost(
        target_pressure_pa=100.0, offgas_mol_per_s=0.0, duration_s=3600.0, **_B259_BASE
    )
    assert (missing.status, missing.feasible) != (proven_zero.status, proven_zero.feasible)


def test_a_real_pumping_load_is_still_costed():
    """Guard against the fix swallowing the working path.

    Sizing the control correctly takes some care, and getting it wrong is what
    two earlier drafts did. The speed ceiling is the pump and the line in
    SERIES, 1/S_eff = 1/S_pump + 1/C, with S_pump = 50.0 m3/s by default. The
    required speed here is 41.57 m3/s, so C must satisfy
    1/50 + 1/C < 1/41.57, i.e. C > ~247 m3/s. C = 1000.0 clears it (S_eff =
    47.62). Omit C entirely and the answer is
    "missing-validated-line-conductance" with feasible=None; pass C = 100 and
    it is "pump-speed-limit-exceeded" with feasible=False (S_eff = 33.33).
    Both are DIFFERENT honest refusals and neither is what this test is about.

    Two drafts of this test went red against unfixed code for those two
    unrelated reasons, which would have read as the b-259 fix breaking the happy
    path. Recorded because a control that fails for its own reasons is worse
    than no control -- it manufactures a false attribution.

    Note the energy is computed as 56.02 kWh in ALL THREE cases, including the
    two refusals: the function computes the physics and MARKS the verdict rather
    than refusing to compute, which is the correct category-2 behaviour.
    """
    result = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=1.0,
        duration_s=3600.0,
        validated_line_conductance_m3_s=1000.0,
        **_B259_BASE,
    )
    assert result.energy_kWh > 0.0
    assert result.status == "ok"


def test_lunar_vacuum_ambient_vents_free_instead_of_reporting_a_missing_pressure():
    """b-288 / SC-146: ambient 0 is the Moon, not an unfilled field.

    The module docstring already says what should happen on a vacuum body: "the
    ambient is already below any useful process pressure, so evolved offgas VENTS
    OUT for free". The vent-free branch implementing that has always been there.
    A `<= 0.0` guard simply refused before execution could reach it, so the body
    this simulator exists to model hit "missing-ambient-pressure".

    The absence is the statement: only the five Mars feedstocks declare
    surface_pressure_mbar (6 mbar). Lunar entries declare none BECAUSE the Moon
    has none. Rewriting that proven zero into NaN made a real lunar ambient
    indistinguishable from a field nobody filled in.

    Note this file states the correct rule for the offgas rate a few lines above
    the guards it fixes -- non-finite is missing, negative is invalid, exactly 0.0
    is a proven zero -- and then did not apply it to either pressure.
    """

    from simulator.pumping_cost import estimate_subambient_pump_cost

    for target_pa, label in ((100.0, "a 1 mbar process hold"), (0.0, "a vacuum hold")):
        cost = estimate_subambient_pump_cost(
            target_pressure_pa=target_pa,
            offgas_mol_per_s=2.0,
            duration_s=3600.0,
            ambient_pressure_pa=0.0,
        )
        assert cost.regime == "vent-free", label
        assert cost.feasible is True, label
        assert cost.energy_kWh == pytest.approx(0.0), label


def test_absolute_vacuum_target_against_a_real_atmosphere_names_why_it_is_impossible():
    """b-288: infinite work is a physical verdict, not a malformed input.

    Reached only when ambient > 0, since ambient 0 vents free. Holding absolute
    zero against a real atmosphere diverges as log(ambient/target) -> inf. That
    is genuinely infeasible, but reporting it as "invalid-target-pressure" told
    an operator their number was malformed when in fact their goal was impossible.
    """

    from simulator.pumping_cost import estimate_subambient_pump_cost

    cost = estimate_subambient_pump_cost(
        target_pressure_pa=0.0,
        offgas_mol_per_s=2.0,
        duration_s=3600.0,
        ambient_pressure_pa=600.0,
    )
    assert cost.feasible is False
    assert cost.regime == "unreachable-absolute-vacuum-target"


def test_pumping_still_refuses_genuinely_unknown_and_impossible_pressures():
    """b-288 non-target: only the proven zero was admitted.

    Passes both before and after the change. It is here so a later relaxation
    that admits NaN or a negative pressure -- "for symmetry with the zero case" --
    goes red. Zero is a physical state; NaN is an absence of knowledge; a negative
    pressure is not a pressure.
    """

    import math

    from simulator.pumping_cost import estimate_subambient_pump_cost

    for kwargs, expected in (
        ({"ambient_pressure_pa": math.nan}, "invalid-ambient-pressure"),
        ({"ambient_pressure_pa": -1.0}, "invalid-ambient-pressure"),
        ({"target_pressure_pa": math.nan, "ambient_pressure_pa": 600.0}, "invalid-target-pressure"),
        ({"target_pressure_pa": -1.0, "ambient_pressure_pa": 600.0}, "invalid-target-pressure"),
    ):
        call = {
            "target_pressure_pa": 100.0,
            "offgas_mol_per_s": 2.0,
            "duration_s": 3600.0,
            "ambient_pressure_pa": 600.0,
        }
        call.update(kwargs)
        cost = estimate_subambient_pump_cost(**call)
        assert cost.feasible is False
        assert cost.regime == expected


def test_mars_pumping_is_unchanged_by_the_vacuum_relaxation():
    """b-288: the Mars path is the one that actually costs energy. Pin it."""

    from simulator.pumping_cost import estimate_subambient_pump_cost

    cost = estimate_subambient_pump_cost(
        target_pressure_pa=100.0,
        offgas_mol_per_s=2.0,
        duration_s=3600.0,
        ambient_pressure_pa=600.0,
    )
    assert cost.regime == "pump"
    assert cost.energy_kWh > 0.0
