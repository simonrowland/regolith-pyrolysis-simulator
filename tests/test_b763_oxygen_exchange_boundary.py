"""Pump-limited headspace boundary for the oxygen-reservoir exchange.

An open pipe settles where melt release equals bleed removal. A closed
pipe keeps the two-capacitor step. The reverse leg stays clamped to real O2.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL, PyrolysisSimulator
from simulator.fe_redox import (
    floor_vacuum_pressure_bar,
    kress91_ln_fO2_temperature_delta,
)
from simulator.feedstock_composition import (
    feot_equivalent_moles,
    iron_oxide_values,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import _required_stage0_carbon_kg
from simulator.state import Atmosphere


DATA = Path(__file__).resolve().parents[1] / "data"

# Mars C2A snapshot 24, capacitor removed. Diagnosis §2.3.
_MARS_HOUR24_T_C = 1365.0
_MARS_HOUR24_LOG_FO2 = -4.33933
_MARS_HOUR24_DN_MOL = 37.97


def _sim() -> PyrolysisSimulator:
    setpoints = yaml.safe_load((DATA / "setpoints.yaml").read_text()) or {}
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"] = True
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        setpoints,
        yaml.safe_load((DATA / "feedstocks.yaml").read_text()) or {},
        yaml.safe_load((DATA / "vapor_pressures.yaml").read_text()) or {},
    )


def _mars_at_hour24() -> PyrolysisSimulator:
    feedstocks = yaml.safe_load((DATA / "feedstocks.yaml").read_text()) or {}
    carbon_kg = _required_stage0_carbon_kg(feedstocks["mars_basalt"], 1000.0)
    sim = _sim()
    sim.load_batch(
        "mars_basalt",
        mass_kg=1000.0,
        additives_kg={"C": carbon_kg},
    )
    sim.melt.temperature_C = _MARS_HOUR24_T_C
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0
    sim.melt.p_total_mbar = 10.0
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = _MARS_HOUR24_LOG_FO2
    sim.melt.oxygen_reservoir.reference_T_K = _MARS_HOUR24_T_C + 273.15
    sim._sync_oxygen_reservoir_mirror()
    return sim


def test_pumped_vacuum_settles_where_release_equals_removal() -> None:
    sim = _mars_at_hour24()
    before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    melt_before = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    fe2o3_before = float(melt_before.get("Fe2O3", 0.0))
    feo_before = float(melt_before.get("FeO", 0.0))
    reservoir = sim._apply_oxygen_reservoir_exchange()

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    x_m = before * math.log(10.0)
    p_floor = 1.0e-9
    p_eq = math.exp(x_m)
    p_h = reservoir.settled_headspace_pO2_bar
    kinetic = alpha * c_m * (x_m - math.log(p_floor))
    dn_at_settled = alpha * c_m * (x_m - math.log(p_h))
    x_after = reservoir.melt_intrinsic_fO2_log * math.log(10.0)

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_clamped is False
    assert reservoir.exchange_notice == {}
    # 10**(-4.33933) bar = 4.58 Pa. The unconstrained kinetic rate is the
    # old fixed-floor sink, ~38 mol/h. The pipe that passes that rate
    # needs 57.7 Pa, so the balance sits below p_eq and dn is far smaller.
    assert p_eq == pytest.approx(4.58e-5, rel=1e-3)
    assert reservoir.kinetic_exchange_o2_mol == pytest.approx(kinetic, rel=1e-9)
    assert reservoir.kinetic_exchange_o2_mol == pytest.approx(
        _MARS_HOUR24_DN_MOL, rel=1e-3
    )
    assert p_floor < p_h < p_eq
    assert reservoir.exchange_o2_mol == pytest.approx(dn_at_settled, rel=1e-6)
    assert reservoir.exchange_o2_mol < 2.0
    assert reservoir.exchange_o2_mol == pytest.approx(
        sim._headspace_o2_removal_at_settled_pO2_bar(p_h),
        rel=1e-3,
    )
    assert 0.0 < alpha < 1.0
    assert x_after == pytest.approx(
        (1.0 - alpha) * x_m + alpha * math.log(p_h),
        rel=1e-9,
    )
    assert min(x_m, math.log(p_h)) < x_after < max(x_m, math.log(p_h))
    dn = reservoir.exchange_o2_mol
    melt = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    # Fe2O3 -> 2 FeO + 1/2 O2: dn mol O2 removes 2*dn Fe2O3 and adds 4*dn FeO.
    assert melt.get("Fe2O3", 0.0) == pytest.approx(fe2o3_before - 2.0 * dn, rel=1e-9)
    assert melt.get("FeO", 0.0) == pytest.approx(feo_before + 4.0 * dn, rel=1e-9)
    credited = sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    assert credited == pytest.approx(dn, rel=1e-9)
    assert sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) == (
        pytest.approx(0.0, abs=1e-15)
    )
    transition = next(
        t for t in reversed(sim.atom_ledger.transitions)
        if t.name == "oxygen_reservoir_exchange"
    )
    touched = {lot.account for lot in transition.debits + transition.credits}
    assert touched == {"process.cleaned_melt", "process.overhead_gas"}

    bleed = sim._dispatch_overhead_bleed()
    residual = sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    assert float(bleed.diagnostic.get("bled_o2_mol", 0.0)) == pytest.approx(
        dn,
        rel=1e-6,
    )
    assert residual < OXYGEN_RESERVOIR_NOOP_MOL

    # The next hour still draws the melt's remaining Fe2O3. The buffer
    # stays empty; a negative-balance credit line is not this path.
    fe2o3_after_first = sim.atom_ledger.mol_by_account("process.cleaned_melt")[
        "Fe2O3"
    ]
    second = sim._apply_oxygen_reservoir_exchange()
    assert second.exchange_direction == "melt_to_headspace"
    assert second.exchange_o2_mol > 0.0
    assert second.settled_headspace_pO2_bar > p_floor
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(fe2o3_after_first - 2.0 * second.exchange_o2_mol, rel=1e-9)
    )
    assert sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) == (
        pytest.approx(0.0, abs=1e-15)
    )


def test_removal_forecast_matches_the_bleed_on_the_same_state(monkeypatch) -> None:
    """A pipe-only forecast of the kinetic credit is not the bleed.

    Premise: sol's Mars hour-24 probe asked the pipe whether it could
    pass 37.97 mol and got yes, while a 0.001 kg/h cold train with
    closed relief admits 0.001/M_O2 mol (0.03125 mol at 0.032 kg/mol).
    Algebra: both the forecast and the dispatch call plan_overhead_bleed
    on this holdup, so the debit is the admitted mol, not the candidate.
    Units: capacity kg/h, M kg/mol, removal mol.
    """
    from types import SimpleNamespace

    from simulator.core import OXYGEN_MOLAR_MASS_KG_PER_MOL
    from simulator.thermal_train import FiniteCapacity

    sim = _mars_at_hour24()
    arriving_mol = _MARS_HOUR24_DN_MOL
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": arriving_mol},
        source="b763 cold-train probe",
        material_origin="feedstock",
    )
    capacity_kg_hr = 0.001
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (
            FiniteCapacity(capacity_kg_hr),
            SimpleNamespace(
                accumulator_enabled=False,
                relief={
                    "k_relief_kg_hr_Pa": 1.0e-6,
                    "p_open_Pa": 1.0e12,
                    "vessel_rating_Pa": 1.0e13,
                },
            ),
        ),
    )

    forecast = sim._headspace_one_hour_o2_removal_mol(0.0)
    bleed = sim._dispatch_overhead_bleed()
    diagnostic = dict(bleed.diagnostic or {})
    admitted_mol = capacity_kg_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL

    assert forecast == diagnostic["bled_o2_mol"]
    assert forecast == pytest.approx(admitted_mol, rel=1e-12)
    assert diagnostic["candidate_bled_o2_mol"] == pytest.approx(
        arriving_mol,
        rel=1e-6,
    )
    assert forecast < 0.1


def test_open_slow_pipe_is_pump_limited_near_equilibrium() -> None:
    """A configured conductance is an open pipe, not a sealed capacitor.

    1e-7 kg/s is constant in pressure. With vacuum downstream the removal
    is flat, so R(p_h*) equals that rate and p_h* sits just below p_eq.
    """
    from simulator.core import OXYGEN_MOLAR_MASS_KG_PER_MOL

    sim = _mars_at_hour24()
    sim._overhead_headspace_config["conductance_kg_s"] = 1.0e-7
    before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    reservoir = sim._apply_oxygen_reservoir_exchange()
    p_eq = 10.0 ** before
    rate_mol_h = 1.0e-7 * 3600.0 / OXYGEN_MOLAR_MASS_KG_PER_MOL

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    # R(p) = alpha * C_m * ln(p_eq / p) equals the flat pipe rate, so
    # p_h* = p_eq * exp(-rate / (alpha * C_m)), just below p_eq.
    expected_p = p_eq * math.exp(-rate_mol_h / (alpha * c_m))

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.kinetic_exchange_o2_mol > 30.0
    assert reservoir.exchange_o2_mol == pytest.approx(rate_mol_h, rel=1e-3)
    assert reservoir.settled_headspace_pO2_bar == pytest.approx(expected_p, rel=1e-4)
    assert reservoir.settled_headspace_pO2_bar < p_eq


def test_closed_pipe_keeps_the_two_capacitor_exchange() -> None:
    sim = _mars_at_hour24()
    sim._overhead_headspace_config["conductance_kg_s"] = 0.0
    before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    reservoir = sim._apply_oxygen_reservoir_exchange()

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    c_h = reservoir.headspace_capacity_mol_per_ln_pO2
    delta_ln = before * math.log(10.0) - math.log(1.0e-9)
    dn_closed = alpha * (delta_ln / (1.0 / c_m + 1.0 / c_h))

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.settled_headspace_pO2_bar == pytest.approx(0.0, abs=0.0)
    assert reservoir.exchange_o2_mol == pytest.approx(dn_closed, rel=1e-6, abs=1e-15)
    assert reservoir.kinetic_exchange_o2_mol > 30.0


def test_partial_melt_above_kress_floor_keeps_the_isoferric_endpoint() -> None:
    """Below a projected liquidus, a partial melt with Kress capacity
    still takes the fixed-redox temperature endpoint. Glass does not.
    """
    sim = _mars_at_hour24()
    reference_C = 1215.0
    target_C = 1305.0
    original_fO2 = -5.0
    partial = {
        "source": "test_partial_melt",
        "solidus_T_C": 500.0,
        "liquidus_T_C": 1800.0,
        "path": ((500.0, 0.0), (1800.0, 1.0)),
    }
    sim.melt.temperature_C = target_C
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = reference_C + 273.15
    sim._sync_oxygen_reservoir_mirror()

    sim._re_reference_melt_fO2_to_temperature(
        target_C + 273.15,
        gate_authority=partial,
    )

    pressure_bar = floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )
    expected = original_fO2 + (
        kress91_ln_fO2_temperature_delta(
            reference_C + 273.15,
            target_C + 273.15,
            reference_pressure_bar=pressure_bar,
            target_pressure_bar=pressure_bar,
        )
        / math.log(10.0)
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(
        target_C + 273.15,
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        expected,
        abs=1.0e-9,
    )

    glass = {
        "source": "test_glass",
        "solidus_T_C": 1500.0,
        "liquidus_T_C": 1800.0,
        "path": ((1500.0, 0.0), (1800.0, 1.0)),
    }
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = reference_C + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim._re_reference_melt_fO2_to_temperature(
        target_C + 273.15,
        gate_authority=glass,
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(
        reference_C + 273.15,
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        original_fO2,
    )

    sim.melt.temperature_C = 1100.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = reference_C + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim._re_reference_melt_fO2_to_temperature(
        1100.0 + 273.15,
        gate_authority=partial,
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(
        reference_C + 273.15,
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        original_fO2,
    )

    sim.melt.temperature_C = target_C
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = reference_C + 273.15
    sim._sync_oxygen_reservoir_mirror()
    melt_before = sim.atom_ledger.project_account_mol("process.cleaned_melt")
    _feo_before, fe2o3_before = iron_oxide_values(melt_before)
    n_fe = feot_equivalent_moles(melt_before)
    ledger_before = (2.0 * fe2o3_before) / n_fe
    reservoir = sim._apply_oxygen_reservoir_exchange(gate_authority=partial)
    # Liquid fraction on the straight solidus-liquidus path. The flux
    # capacity is that fraction of the bulk Kress capacity; the scalar
    # still moves by -dn / C_full so the bulk ledger tracks Kress.
    liquid_fraction = (target_C - 500.0) / (1800.0 - 500.0)
    c_flux = reservoir.melt_redox_capacity_mol_per_ln_fO2
    c_full = c_flux / liquid_fraction
    dn = reservoir.exchange_o2_mol
    assert dn > 0.0
    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.reference_T_K == pytest.approx(target_C + 273.15)
    assert c_flux == pytest.approx(c_full * liquid_fraction, rel=1e-12)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(
        expected - (dn / c_full) / math.log(10.0),
        abs=1.0e-9,
    )
    melt_after = sim.atom_ledger.project_account_mol("process.cleaned_melt")
    _feo_after, fe2o3_after = iron_oxide_values(melt_after)
    ledger_after = (2.0 * fe2o3_after) / feot_equivalent_moles(melt_after)
    # Bulk debit: Δ(Fe3+/FeT) = -4 dn / N_Fe exactly. The scalar step
    # -dn/C_full is the Kress derivative of that same change.
    assert fe2o3_after == pytest.approx(fe2o3_before - 2.0 * dn, rel=1e-9)
    assert ledger_after - ledger_before == pytest.approx(
        -4.0 * dn / n_fe,
        abs=1e-12,
    )
    pressure_bar = floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )
    comp = sim._melt_oxide_wt_pct()
    implied_at_endpoint = sim._fe3_over_sigma_fe_at_fO2(
        comp,
        fO2_log=expected,
        T_K=target_C + 273.15,
        pressure_bar=pressure_bar,
    )
    implied_after = sim._fe3_over_sigma_fe_at_fO2(
        comp,
        fO2_log=reservoir.melt_intrinsic_fO2_log,
        T_K=target_C + 273.15,
        pressure_bar=pressure_bar,
    )
    assert implied_after - implied_at_endpoint == pytest.approx(
        ledger_after - ledger_before,
        abs=1e-4,
    )


def test_subliquidus_hour_does_not_exchange() -> None:
    sim = _mars_at_hour24()
    sim.melt.temperature_C = 1100.0
    sim.melt.oxygen_reservoir.reference_T_K = None
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()
    fe2o3_before = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    transitions_before = len(sim.atom_ledger.transitions)

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.reference_T_K is None
    assert reservoir.exchange_o2_mol == pytest.approx(0.0, abs=1e-15)
    assert reservoir.exchange_direction == (
        "none:solid_state_ferric_decomposition_not_modelled"
    )
    assert reservoir.exchange_notice["flagged"] is True
    assert reservoir.exchange_notice["message"] == (
        "solid-state ferric decomposition not modelled"
    )
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    ) == pytest.approx(fe2o3_before)
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0) == (
        pytest.approx(0.0, abs=1e-15)
    )
    assert len(sim.atom_ledger.transitions) == transitions_before


def test_exchange_then_respeciation_does_not_credit_the_same_dn() -> None:
    sim = _mars_at_hour24()
    reservoir = sim._apply_oxygen_reservoir_exchange()
    dn = reservoir.exchange_o2_mol
    overhead_after_exchange = sim.atom_ledger.mol_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)

    diagnostic = sim._apply_fe_redox_respeciation()

    overhead_after_respeciation = sim.atom_ledger.mol_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    residual = overhead_after_respeciation - overhead_after_exchange
    divergence = diagnostic["ferric_divergence_after"]
    assert dn > 0.0
    assert reservoir.kinetic_exchange_o2_mol > 30.0
    assert diagnostic["status"] == "ok"
    # Curvature may credit a residual. A second copy of dn would be ~dn.
    assert residual < 0.2 * dn
    assert divergence["delta_abs"] <= 0.05


def test_reverse_exchange_without_inventory_stays_clamped() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 10.0
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim.melt.oxygen_reservoir.reference_T_K = 1600.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "none:headspace_o2_clamped"
    assert reservoir.exchange_clamped is True
    assert reservoir.exchange_o2_mol == pytest.approx(0.0, abs=1e-15)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(-10.0)
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0) == (
        pytest.approx(0.0, abs=1e-15)
    )
