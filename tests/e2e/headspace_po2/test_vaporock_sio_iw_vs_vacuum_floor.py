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
Kress91 fO2 (~10^-8 bar at 1570 C) drives the IW comparison.  The
assertion (``|log10 ratio| <= 0.3 decade``) is unchanged -- the new
anchor exercises it where SiO physics is actually active.

Anchor: ``CampaignPhase.C2A``, ``start_temperature_C=1550``, hour 6
(``T~=1577.5 C``).  The 6-hour preamble lets the C2A ramp lift the melt
into the SiO peak window past Antoine's lower edge and lets the
finite-headspace bleed reach steady state under PN2_SWEEP.
"""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.state import CampaignPhase

from .helpers import run_campaign_headspace


SIO_ANCHOR_CAMPAIGN = CampaignPhase.C2A
SIO_ANCHOR_START_TEMPERATURE_C = 1550.0
SIO_ANCHOR_HOUR = 6


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
        control_inputs={"pO2_bar": 10.0 ** fO2_log_iw},
    )
    p_sio_iw = dict(iw_result.diagnostic or {}).get(
        "vapor_pressures_Pa", {}
    ).get("SiO")

    if not p_sio_finite or not p_sio_iw:
        pytest.fail(
            "hot-C2A pinned SiO ratio unavailable: "
            f"campaign={SIO_ANCHOR_CAMPAIGN.name}, "
            f"hour={SIO_ANCHOR_HOUR}, "
            f"T={temperature_C:.1f} C, "
            f"p_SiO_finite={p_sio_finite}, p_SiO_IW={p_sio_iw}"
        )

    decade = abs(math.log10(p_sio_finite / p_sio_iw))
    assert decade <= 0.3, (
        f"finite-pO2 vs IW SiO ratio drifted: "
        f"|log10({p_sio_finite:.4g} / {p_sio_iw:.4g})| = "
        f"{decade:.4f} decade > 0.3"
    )
