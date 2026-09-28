"""SiO finite-pO2 vs IW-buffer regression in the hot SiO window.

The Phase 1 design pinned hour 12 of ``lunar_mare_low_ti x C0 x 24h`` as
the steady-state anchor, but C0 is the 20-950 C vacuum bakeoff -- at
hour 12 the melt sits at ~585 C, well below the SiO Antoine valid range
(``valid_range_K: [1400, 2200]`` -> >=1126.85 C).  Below that floor the
fallback equilibrium emits no ``vapor_pressures_Pa['SiO']`` entry and
the finite-pO2 vs IW ratio is undefined.

Phase 2 retargets the anchor to C2A's peak SiO window (1400-1600 C),
the regime the finite-headspace pO2 model is designed to validate: the
PN2 sweep drains O2 every tick so ``_commanded_pO2_bar`` collapses to
the numerical vacuum floor (~1e-9 bar) while the melt's intrinsic
Kress91 fO2 (~10^-8 bar at 1570 C) drives the IW comparison.  After the
pO2-fix, VapoRock consumes the commanded pO2 directly, so SiO rises
against the IW comparison by the expected pO2^-0.5 lever.

Anchor: ``CampaignPhase.C2A``, ``start_temperature_C=1550``, hour 6
(``T~=1577.5 C``).  The 6-hour preamble lets the C2A ramp lift the melt
into the SiO peak window past Antoine's lower edge and lets the
finite-headspace bleed reach steady state under PN2_SWEEP.
"""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import OXYGEN_SPECIES
from simulator.state import Atmosphere, CampaignPhase

from .helpers import build_headspace_sim, run_campaign_headspace


SIO_ANCHOR_CAMPAIGN = CampaignPhase.C2A
SIO_ANCHOR_START_TEMPERATURE_C = 1550.0
SIO_ANCHOR_HOUR = 6
# For SiO2(l) -> SiO(g) + 1/2 O2(g), p_SiO = P_ref*a_SiO2*sqrt(p_ref/pO2).
# Compare that mass-action relation using each branch's activity and interface pO2.


def test_vaporock_sio_iw_vs_vacuum_floor_hot_c2a_anchor():
    sim, _snapshots, hour_trace, _sio_cumulative_kg = run_campaign_headspace(
        enabled=True,
        hours=SIO_ANCHOR_HOUR,
        campaign=SIO_ANCHOR_CAMPAIGN,
        start_temperature_C=SIO_ANCHOR_START_TEMPERATURE_C,
    )
    anchor = hour_trace[SIO_ANCHOR_HOUR]
    p_sio_finite = anchor["p_SiO_Pa"]
    temperature_C = anchor["temperature_C"]
    fO2_log_iw = sim._compute_intrinsic_melt_fO2(temperature_C + 273.15)
    iw_result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=temperature_C,
        pressure_bar=max(sim.melt.p_total_mbar / 1000.0, 1.0e-9),
        fO2_log=fO2_log_iw,
        control_inputs={
            "pO2_bar": 10.0 ** fO2_log_iw,
            "interface_pO2_bar": 10.0 ** fO2_log_iw,
        },
    )
    p_sio_iw = dict(iw_result.diagnostic or {}).get(
        "vapor_pressures_Pa", {}
    ).get("SiO")
    finite_provenance = sim._last_vapor_pressure_diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["SiO"]
    iw_provenance = dict(iw_result.diagnostic or {})[
        "vapor_pressure_numerator_provenance"
    ]["SiO"]

    if not p_sio_finite or not p_sio_iw:
        pytest.fail(
            "hot-C2A pinned SiO ratio unavailable: "
            f"campaign={SIO_ANCHOR_CAMPAIGN.name}, "
            f"hour={SIO_ANCHOR_HOUR}, "
            f"T={temperature_C:.1f} C, "
            f"p_SiO_finite={p_sio_finite}, p_SiO_IW={p_sio_iw}"
        )

    expected_ratio = (
        finite_provenance["activity_factor"]
        / iw_provenance["activity_factor"]
        * math.sqrt(iw_provenance["pO2_bar"] / finite_provenance["pO2_bar"])
    )
    assert p_sio_finite / p_sio_iw == pytest.approx(
        expected_ratio,
        rel=5.0e-4,
    )


def test_pn2_sweep_sio_provider_uses_transport_floor_not_holdup_reservoir():
    sim = build_headspace_sim(
        enabled=True,
        campaign=SIO_ANCHOR_CAMPAIGN,
        start_temperature_C=SIO_ANCHOR_START_TEMPERATURE_C,
    )
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    requested_transport_pO2_bar = sim._vacuum_floor_bar()
    sim.melt.pO2_mbar = requested_transport_pO2_bar * 1000.0
    sim.melt.p_total_mbar = 10.0

    holdup_o2_mol = 1.0e-3
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {OXYGEN_SPECIES: holdup_o2_mol},
        source="test PN2 holdup O2 must not drive SiO transport pO2",
        material_origin="feedstock",
    )

    sim._apply_oxygen_reservoir_exchange()
    sim._apply_native_fe_saturation_split(sample_time_h=0.0)
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_ledger_pO2_bar > requested_transport_pO2_bar * 1.0e5
    assert requested_transport_pO2_bar * 1.0e5 == pytest.approx(1.0e-4)
    assert reservoir.headspace_transport_pO2_bar == pytest.approx(
        requested_transport_pO2_bar
    )

    equilibrium = sim._get_equilibrium()
    diagnostic = dict(sim._last_vapor_pressure_diagnostic or {})
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["SiO"]
    p_sio = diagnostic["vapor_pressures_Pa"]["SiO"]
    interface_pO2_bar = sim.melt.oxygen_reservoir.interface_pO2_bar

    assert diagnostic["pO2_bar"] == pytest.approx(requested_transport_pO2_bar)
    assert provenance["pO2_bar"] == pytest.approx(interface_pO2_bar)
    assert interface_pO2_bar >= requested_transport_pO2_bar
    assert p_sio == pytest.approx(provenance["P_eq_Pa"])
    assert equilibrium.vapor_pressures_Pa["SiO"] == pytest.approx(p_sio)

    holdup_substituted_p_sio = p_sio * math.sqrt(
        requested_transport_pO2_bar / reservoir.headspace_ledger_pO2_bar
    )
    assert p_sio > holdup_substituted_p_sio * 100.0
