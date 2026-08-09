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
# 2026-07-11 0.5.10 E-MOVE: K/S Kress re-reference plus BCD oxygen/native-state
# routing lower the finite-pO2/IW SiO drift.
# 2026-07-18 a91db36 loaded-melt/flow-boundary trajectory rebaseline on
# corrected tip 0990232.
# 2026-08-05 MC-4 wave 1B (a34318c): the Si-family carrier union (active Si
# metal standard-reaction row, plus the pO2-insensitive SiO2(g) gas-exchange
# channel drawing ~2% of the SiO2 pool) moves the finite-pO2/IW separation
# 0.1844 -> 0.2044 decade. The SiO Antoine row itself is bit-identical to
# the base; the hard AtomLedger closure is unaffected.
# 2026-08-06 b-145: physical-composite OOR on composite oxide carriers
# (SiO2_gas, Si2, Si3, …) removes the +multi-dex invented low-T pressures
# from slope continuation. Pure SiO Antoine OOR is unchanged, but the
# managed finite-pO2 headspace trajectory (self-oxidation / co-evaporation
# of the composite Si family) shifts the finite-pO2 vs IW SiO separation
# 0.2044 -> 0.1858 decade (DOWN). Sign check: lower composite low-T
# pressures ⇒ less early Si-family loss ⇒ milder managed-branch
# self-oxidation drift vs IW ⇒ smaller decade separation. Closure
# unaffected; pin is the executed value under physical composite OOR.
# 2026-08-08 t-383: Na coherent pair (L&H liquid-NaO0.5 standard_reaction_term
# + γ=1e−3) replaces Chase gas_standard_fugacity. SIGN CHECK: Na pressures UP
# (+0.118 dex at the 1429 K investigation cell) ⇒ more Na volatilisation on the
# managed finite-pO2 branch. SiO Antoine row is bit-identical; the finite/IW
# SiO separation moves 0.1858 → 0.2103 decade (UP) via the alkali-coupled
# headspace trajectory (self-oxidation / co-evaporation of volatiles under the
# managed pO2 path vs the IW-buffered control). Pin is the executed value
# under the landed L&H Pref; never hand-pasted.
# 2026-08-09 b-151: Na2/Na2O_gas composite base_reference retargeted from the
# retired activity-folded pseudo_psat (A=5.18586…) to monatomic L&H Pref
# (A=11.342243 / B=12140.316409 / C=−163.701). Unit-activity Na2 P up
# +1.95…+6.26 dex (base^2). SIGN CHECK at hot C2A hour-6 anchor:
#   cum Na-family flux 0.1293 → 0.1682 kg (+30%); melt Na2O Δ −0.121 → −0.155 kg;
#   p_SiO_finite 1.07465 → 1.07475 Pa (≈flat; managed pO2 floor 1e−9 both legs);
#   p_SiO_IW 0.66213 → 0.64839 Pa (DOWN).
# IW branch driver is fO2_log_iw = _compute_intrinsic_melt_fO2(T), NOT the
# managed-path melt fO2_log hour-trace series (−8.3739 → −8.3105). Correct
# lever series (probe sio_{before,after}.json top-level fO2_log_iw):
#   fO2_log_iw −8.579295695005907 → −8.560997361998243 (Δ = +0.018298333007663814);
#   pO2 factor 10^Δ ≈ 1.04303; p_SiO ∝ pO2^−0.5 ⇒ scale 1/√factor ≈ 0.979154;
#   0.6621306185414277 × 0.979154 ≈ 0.648328 (vs observed 0.648391; residual
#   ~9.8e−5 relative — a_SiO2 / melt-path detail, not a missed lever).
#   decade |log10(finite/IW)| 0.2103247134402787 → 0.21947209153015898 (UP).
# Same alkali-coupled class as t-383; SiO Antoine row bit-identical; not a
# mass-balance artifact (AtomLedger closes). Pin = executed probe value;
# never hand-pasted. See docs-private/research/2026-08-09-b151-disposition/.
EXPECTED_SIO_DECADE_DRIFT = 0.21947209153015898


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

    assert diagnostic["pO2_bar"] == pytest.approx(requested_transport_pO2_bar)
    assert provenance["pO2_bar"] == pytest.approx(requested_transport_pO2_bar)
    assert p_sio == pytest.approx(provenance["P_eq_Pa"])
    assert equilibrium.vapor_pressures_Pa["SiO"] == pytest.approx(p_sio)

    holdup_substituted_p_sio = p_sio * math.sqrt(
        requested_transport_pO2_bar / reservoir.headspace_ledger_pO2_bar
    )
    assert p_sio > holdup_substituted_p_sio * 100.0
