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
from simulator.state import CampaignPhase

from .helpers import run_campaign_headspace


SIO_ANCHOR_CAMPAIGN = CampaignPhase.C2A
SIO_ANCHOR_START_TEMPERATURE_C = 1550.0
SIO_ANCHOR_HOUR = 6
# 2026-07-02 SSO-R ch1(+1c): the conserved fO2 integrator (heuristic demoted
# to seed-only) shifts the finite-pO2 branch's melt fO2 at the hot C2A
# anchor hour; the IW-vs-finite SiO suppression ratio moves 0.4909 -> 0.5010
# decade. Correction-class (old pin encoded the hourly heuristic re-seed).
# 2026-07-02 SSO-R ch2c: evaporative metal/O-loss coupling — the managed
# finite-pO2 branch now SELF-OXIDIZES over the 6-hour anchor (alkali metal
# vapor leaves, O stays), dropping its p_SiO 0.8619 -> 0.3448 Pa while the
# IW-BUFFERED branch is byte-identical (0.2719 — the buffer absorbs couple
# changes; strong internal control). Separation 0.5010 -> 0.1031 decade;
# the old wide separation partly encoded the missing self-oxidation.
# Correction-class.
# 2026-07-02 re-speciation (#82): retained-O ledger bookkeeping narrows
# the managed-vs-IW separation further (0.1031 -> 0.0342 decade).
# 2026-07-03 LIVE-PO2-SWEEP (#94): PN2 sweep transport pO2 is now computed
# BEFORE vapor dispatch from sweep-balance semantics instead of the
# pre-bleed closed-headspace ledger (native-split O2 no longer crushes the
# managed branch for its own emission tick). The finite-pO2 branch's p_SiO
# rises 0.3724 -> 0.8608 Pa and the managed-vs-IW separation widens
# 0.0342 -> 0.3639 decade. Correction-class: the old pin encoded the
# holdup-O2 ordering bug this docstring's own design statement forbids.
# 2026-07-07 t-141 L&H K standard-term regen: decade drift -0.0076276 via the
# K-coupled headspace path (matches golden-deltas.json enumeration).
EXPECTED_SIO_DECADE_DRIFT = 0.35630359273564843


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
    assert decade == pytest.approx(EXPECTED_SIO_DECADE_DRIFT, abs=5.0e-4), (
        f"finite-pO2 vs IW SiO ratio drifted: "
        f"|log10({p_sio_finite:.4g} / {p_sio_iw:.4g})| = "
        f"{decade:.4f} decade"
    )
