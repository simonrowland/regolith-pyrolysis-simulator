"""SiO step isolation: WALL_DEPOSIT."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

from simulator.condensation import (
    _antoine_psat_pa,
    _hkl_surface_deposition_flux_mol_m2_s,
)
from simulator.runner import build_sio_yield_report


@lru_cache(maxsize=None)
def _report_at_wall_T(liner_temperature_c: float) -> tuple[dict[str, Any], dict[str, float]]:
    return build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=24,
        mass_kg=1000.0,
        include_diagnostics=True,
        liner_temperature_c=liner_temperature_c,
        pO2_mbar=None,
    )


def _sio_wall_deposit_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return float(report["wall_deposit_kg"].get("SiO", 0.0))


def test_wall_deposit_crosses_fast_to_slow_fouling_threshold_at_1400c():
    # Post 2026-05-20 Antoine refit: builtin SiO P_sat dropped ~4700x to the
    # VapoRock-consistent value, so the 1050 C cold-liner deposit fell from
    # 1.05348872049e-2 kg to 2.24808480214e-06 kg.
    # Post-F3 (Knudsen regime enforcement, 2026-05-27): the band-integration
    # HKL flux in `_condensation_efficiency` now applies `regime_factor(Kn)`
    # consistent with the existing docstring (was a code/doc inconsistency).
    # In viscous regime stage-3 HKL is attenuated → less SiO captured at the
    # stage → MORE SiO reaches the walls. Wall deposit at 1050 C climbed
    # from 2.24808480214e-06 to 1.517228591109e-05 kg (~6.75x).
    # Post-r7 autoreview fix (2026-05-27): the equal-temperature wall-routing
    # fast path used to allocate the wall-deposit candidate across EVERY pipe
    # segment, including segments downstream of the species' designated
    # condenser stage that cannot physically see the vapor. The fix mirrors
    # the mixed-temperature branch's _mixed_temperature_wall_candidate_segments
    # restriction (upstream-only) plus the per-segment supply cap. SiO walls
    # now only credit reachable segments, dropping the 1050 C deposit from
    # 1.517228591109e-05 to 6.589955385e-06 kg (~57% reduction). The
    # over-credited downstream wall deposits route into the terminal vent
    # account instead (mass balance still closes; F1 stage-routing-purity
    # report is now honest about which segments physically collect SiO).
    # The fouling-threshold structure (deposit at 1050 C, none at 1400/1500 C)
    # is unchanged.
    # Post-0.5.0 (2026-05-27) thermo-data refresh (MnO NIST-JANAF +
    # autoreview-r8 vapor-pressure unavailable-path raise): 1 PPM
    # numerical drift on the SiO surface (Mn entry change shifts the
    # _stub_equilibrium iteration order which alters FP rounding on
    # downstream dict-iterated quantities). 6.589955385e-06 →
    # 6.5899485456e-06 (rel ~1e-6). No physics change; pure FP noise
    # from a documented thermo-table update.
    # Post-0.5.0 viscous-regime mass-transfer (tickler §5 follow-on):
    # Sherwood-number boundary-layer flux (Bird/Stewart/Lightfoot,
    # Sh=3.66 for laminar pipe flow) added as a regime_factor-weighted
    # companion to HKL. In viscous regime (low Kn) the mass-transfer
    # term dominates; the stage-band integration + wall-candidate are
    # both rebalanced. Net effect on the 1050 C cold-liner wall deposit:
    # 6.5899485456e-06 → 6.46501781604e-06 (~−1.9% relative). The
    # released mass redistributes downstream through the train; total
    # SiO budget conserved. The fouling-threshold structure (deposit at
    # 1050 C, none at 1400/1500 C) is unchanged.
    # Pre-0.5.1 autoreview P2 (2026-05-27): the viscous mass-transfer
    # ideal-gas denominator now uses BULK gas T (`self.gas_temperature_C`)
    # instead of the wall surface T -- which overstated the flux in
    # cold-wall scenarios (e.g. 1050 C liner against 1700 C bulk).
    # Net effect: 1050 C wall deposit climbs slightly to
    # 6.7529006436e-06 (~+4.5% vs the prior wall-T-in-denominator
    # value). Direction is physics-honest: at colder walls, gas T no
    # longer enters the denominator, so the flux is no longer
    # under-divided.
    # 0.5.2 Phase A1 (2026-05-27): Chapman-Enskog D_AB replaces the
    # legacy 1e-2 m²/s constant. At the SiO/N2 typical operating
    # point (10 mbar, ~1973 K bulk gas) the proper D_AB ≈ 4.97e-2
    # m²/s -- ~5× higher than the constant. Net effect on the 1050 C
    # cold-liner wall deposit: 6.7529006436e-06 → 6.9806097730e-06
    # (~+3.4% relative). Direction is physics-honest: higher D_AB
    # means more boundary-layer mass-transfer in viscous regime,
    # which is exactly the gap the viscous-MT model was meant to
    # close.
    # 0.5.2 Phase B (2026-05-27): viscous-regime mass transfer
    # replaced with the canonical series-resistance form
    # ``1/k_total = 1/(α_s·k_HKL) + (1−f)/k_MT`` (Bird/Stewart/Lightfoot),
    # and the Sherwood number now scales with the operator's induction
    # stirring power (``Sh_eff = 3.66 · √stir_factor``, Frössling
    # style; ``stir_factor=6`` default at C2A → Sh ≈ 9.0). The codex
    # P0 #1 challenge had flagged the v1 additive blend
    # ``f·J_HKL + (1−f)·J_MT`` as wrong physics in viscous regime
    # (HKL absolute magnitude dominated the blend at 95%); the series
    # form is regime-correct at both limits without a hand-tuned weight
    # curve. The free-molecular branch still degenerates to pure HKL
    # via the ``(1−f)`` boundary-layer weight. Net effect on the 1050 C
    # cold-liner wall deposit: 6.9806097730e-06 → 8.28395539869e-06
    # (~+18.7% relative). Direction is physics-honest: the cold wall's
    # ΔP is large (P_sat ≈ 0) and stir-enhanced k_MT roughly doubles
    # the wall-flux candidate; series resistance keeps the cold wall
    # competitive with the hotter baffle stages (whose driving
    # pressures are smaller due to higher P_sat). The fouling-threshold
    # structure (deposit at 1050 C, none at 1400/1500 C) is unchanged.
    # 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip
    # exposes backpressure-floor physics; previously the synthetic
    # no-headspace pO2 floor masked the holdup feedback. The C2A
    # PN2_SWEEP atmosphere now reads the real overhead-gas O2 inventory
    # (vacuum-floor 1e-9 bar) instead of the conductance-ratio derived
    # synthetic O2 partial. Lower commanded pO2 → less SiO suppression
    # via the 1/sqrt(pO2) Ellingham factor → ~2.4x more SiO evolves
    # and proportionally more lands on the 1050 C cold wall. Net
    # effect: 8.28395539869e-06 → 2.0039542334640e-05 (~+142%
    # relative). The fouling-threshold structure (deposit at 1050 C,
    # none at 1400/1500 C) is unchanged — the magnitude rises because
    # the new commanded-pO2 is lower (no synthetic floor), so the SiO
    # supply driving the cold wall is larger.
    assert _sio_wall_deposit_kg(1050.0) == pytest.approx(
        2.0039542334640e-05, rel=1e-9
    )
    assert _sio_wall_deposit_kg(1400.0) == 0.0
    assert _sio_wall_deposit_kg(1500.0) == 0.0


def test_hk_wall_deposit_driving_force_has_correct_sign():
    wall_T_K = 1050.0 + 273.15
    p_sat_wall_pa = _antoine_psat_pa("SiO", wall_T_K)
    assert p_sat_wall_pa is not None and p_sat_wall_pa > 0.0

    no_deposit = _hkl_surface_deposition_flux_mol_m2_s(
        "SiO",
        P_local_pa=0.99 * p_sat_wall_pa,
        T_surface_K=wall_T_K,
        alpha_s=0.8,
    )
    deposit = _hkl_surface_deposition_flux_mol_m2_s(
        "SiO",
        P_local_pa=1.01 * p_sat_wall_pa,
        T_surface_K=wall_T_K,
        alpha_s=0.8,
    )

    assert no_deposit == 0.0
    assert deposit > 0.0
