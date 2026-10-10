"""Fixed-pressure headspace boundary for the oxygen-reservoir exchange.

A pumped vacuum is dn = alpha * C_m * (x_m - x_h). A slow bleed keeps
the two-capacitor step. The reverse leg stays clamped to real O2.
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


def test_pumped_vacuum_is_a_fixed_pressure_sink() -> None:
    sim = _mars_at_hour24()
    before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    melt_before = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    fe2o3_before = float(melt_before.get("Fe2O3", 0.0))
    feo_before = float(melt_before.get("FeO", 0.0))
    reservoir = sim._apply_oxygen_reservoir_exchange()

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    x_m = before * math.log(10.0)
    x_h = math.log(1.0e-9)
    dn_fixed = alpha * c_m * (x_m - x_h)
    x_after = reservoir.melt_intrinsic_fO2_log * math.log(10.0)

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_clamped is False
    assert reservoir.exchange_notice == {}
    assert reservoir.exchange_o2_mol == pytest.approx(dn_fixed, rel=1e-9)
    assert reservoir.exchange_o2_mol == pytest.approx(_MARS_HOUR24_DN_MOL, rel=1e-3)
    assert 0.0 < alpha < 1.0
    # Closed form: x_after = (1 - alpha) x_m + alpha x_h, strictly between.
    assert x_after == pytest.approx((1.0 - alpha) * x_m + alpha * x_h, rel=1e-9)
    assert min(x_m, x_h) < x_after < max(x_m, x_h)
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
    assert second.exchange_o2_mol > 1.0
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(fe2o3_after_first - 2.0 * second.exchange_o2_mol, rel=1e-9)
    )
    assert sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) == (
        pytest.approx(0.0, abs=1e-15)
    )


def test_slow_bleed_keeps_the_two_capacitor_exchange() -> None:
    sim = _mars_at_hour24()
    sim._overhead_headspace_config["conductance_kg_s"] = 1.0e-7
    before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    reservoir = sim._apply_oxygen_reservoir_exchange()

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    c_h = reservoir.headspace_capacity_mol_per_ln_pO2
    delta_ln = before * math.log(10.0) - math.log(1.0e-9)
    dn_closed = alpha * (delta_ln / (1.0 / c_m + 1.0 / c_h))

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_o2_mol == pytest.approx(dn_closed, rel=1e-6, abs=1e-15)
    assert reservoir.exchange_o2_mol < 1.0e-6
    # The same hour on the pumped pipe is the ~38 mol/h sink, not this dn.
    assert alpha * c_m * delta_ln > 30.0


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
    fe2o3_before = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    reservoir = sim._apply_oxygen_reservoir_exchange(gate_authority=partial)
    assert reservoir.exchange_o2_mol == pytest.approx(0.0, abs=1e-15)
    assert reservoir.exchange_direction == "none:below_projected_liquidus"
    assert reservoir.reference_T_K == pytest.approx(target_C + 273.15)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected, abs=1.0e-9)
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    ) == pytest.approx(fe2o3_before)


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
    assert dn > 30.0
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
