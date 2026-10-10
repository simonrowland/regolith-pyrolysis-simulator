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
    reservoir = sim._apply_oxygen_reservoir_exchange()

    alpha = 1.0 - math.exp(-1.0 / reservoir.tau_hr)
    c_m = reservoir.melt_redox_capacity_mol_per_ln_fO2
    x_m = before * math.log(10.0)
    x_h = math.log(1.0e-9)
    dn_fixed = alpha * c_m * (x_m - x_h)
    x_after = reservoir.melt_intrinsic_fO2_log * math.log(10.0)

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_clamped is False
    assert reservoir.exchange_o2_mol == pytest.approx(dn_fixed, rel=1e-9)
    assert reservoir.exchange_o2_mol == pytest.approx(_MARS_HOUR24_DN_MOL, rel=1e-3)
    assert 0.0 < alpha < 1.0
    # Closed form: x_after = (1 - alpha) x_m + alpha x_h, strictly between.
    assert x_after == pytest.approx((1.0 - alpha) * x_m + alpha * x_h, rel=1e-9)
    assert min(x_m, x_h) < x_after < max(x_m, x_h)
    credited = sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    assert credited == pytest.approx(reservoir.exchange_o2_mol, rel=1e-9)

    bleed = sim._dispatch_overhead_bleed()
    residual = sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    assert float(bleed.diagnostic.get("bled_o2_mol", 0.0)) == pytest.approx(
        reservoir.exchange_o2_mol,
        rel=1e-6,
    )
    assert residual < OXYGEN_RESERVOIR_NOOP_MOL

    # A trace of tracked buffer oxygen must not refuse the next hour's draw.
    # Evaporation parks that trace; the credit limit still covers the sink.
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 1.0e-6},
        source="trace parked oxygen",
        material_origin="feedstock",
    )
    second = sim._apply_oxygen_reservoir_exchange()
    assert second.exchange_direction == "melt_to_headspace"
    assert second.exchange_o2_mol > 1.0


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
