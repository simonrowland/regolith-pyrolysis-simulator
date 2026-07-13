"""SiO step isolation: WALL_DEPOSIT."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

from simulator import condensation as condensation_module
from simulator.condensation import (
    _antoine_psat_pa,
    _hkl_impingement_flux_mol_m2_s,
    _hkl_surface_deposition_flux_mol_m2_s,
    _series_resistance_deposition_flux_mol_m2_s,
    _sticking_reactivity_class,
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
        # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
        allow_unmeasured_alpha_fallback=True,
    )


def _sio_wall_product_deposit_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    wall = report["wall_deposit_kg"]
    assert float(wall.get("SiO", 0.0)) == pytest.approx(0.0)
    return float(wall.get("Si", 0.0)) + float(wall.get("SiO2", 0.0))


def test_wall_deposit_is_rebaselined_after_corrected_hkl_mass_flux():
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
    # _internal_analytical_equilibrium iteration order which alters FP rounding on
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
    # P1-A HKL mass-flux fix (2026-06-04): EVAPORATION_FLUX now projects
    # molar HKL flux to kg with sqrt(M) in the numerator. SiO evolution and
    # downstream wall deposit scale by M_SiO=0.04408:
    # 2.0039542334640e-05 -> 8.82956746206e-07 kg after
    # canonical Si/O atomic weights.
    # 2026-06-14 dense VapoRock pseudo-Antoine refit lifts the fallback
    # SiO wall-deposit baseline to 1.250623477194e-05 kg while preserving
    # the threshold structure.
    # 2026-06-15 NIST pure-component runtime reroute replaces the legacy
    # Si vapor pressure with the grounded sidecar. SiO supply drops, and
    # the 1050 C wall-deposit baseline follows:
    # 1.250623477194e-05 -> 5.35761509103e-06 kg.
    # 2026-06-15 Mn/Ti Alcock/CRC grounding slightly shifts the coupled
    # fallback iteration and updates this trace wall baseline:
    # 5.35761509103e-06 -> 5.35761631701e-06 kg.
    # 2026-06-19 BUG-013: N2 collision diameter grounded to the BSL Table
    # E.1 Lennard-Jones sigma (3.7e-10 -> 3.798e-10 m). The MFP/Knudsen
    # regime_factor drops ~4.9%, but viscous-regime deposition is mass-
    # transfer-dominated, so the wall baseline moves only -0.0015%:
    # 5.35761631701e-06 -> 5.357536728e-06 kg.
    # 2026-06-21 BUG-101: fused-silica SiO alpha_s is now differentiated
    # from the upstream liner proxies and fail-closes at zero because no
    # direct sticking coefficient is cited. The 1050 C cold-wall baseline
    # now excludes that fused-silica segment:
    # 5.357536728e-06 -> 3.58623058352e-06 kg.
    # 2026-06-23 D4: default SiO alpha_s is read from
    # data/literature/vacuum_pyrolysis_sticking.yaml (REF-018/REF-016,
    # 0.04) instead of the legacy by-feel 0.7. This lowers the
    # pressure-isolated capture budget and the 1050 C wall deposit:
    # 3.58623058352e-06 -> 4.6778715958e-07 kg.
    # 2026-06-28 alpha-series source model removes the final stir multiplier
    # and adds gas/melt resistances upstream of deposition. The same cold-wall
    # capture physics sees much less SiO source:
    # 4.6778715958e-07 -> 8.21353261008e-09 kg.
    # 2026-06-30 cold-wall SiO condensation uses the Pound 1972 unity
    # high-supersaturation gate below the evaporation-Arrhenius validity floor.
    # Hot-wall capture remains on the Wetzel/Gail Arrhenius.
    # 2026-07-01 C4b stores wall SiO as physical products instead of a SiO
    # proxy. The 1050 C case drops because same-run Mg consumes some SiO2 into
    # MgO; the hot-wall cases retain the same Si+SiO2 mass to rounding.
    # 2026-07-02 SSO-R ch2c evaporative-coupling ripple: +0.14% at 1050 C
    # (fO2 trajectory shift through the coupled route). Correction-class.
    # 2026-07-06 CF-3 constant gamma*X alkali activity lowers Na/K vapor
    # backpressure, moving the coupled SiO wall pins without changing the
    # fouling-threshold structure.
    # 2026-07-11 0.5.10 E-MOVE: phase-basis/two-rail vapor plus K/S fO2 and
    # alkali-path changes lower the coupled wall-product pins.
    # 2026-07-12 accounting-closure rebaseline: e73fde5 replaced the
    # dimensionally invalid capture efficiency with the direct per-segment
    # wall budget J * A * M * 3600, capped by available supply. Recomputed on
    # combined main; attribution: docs-private/reviews/2026-07-11-wave08/
    # runtime-golden-attribution.md.
    # 2026-07-12 wave-10 wall-flux closeout: the area-integrated wall-flux
    # contract is now restored end-to-end after the process-condensation fold,
    # and wave-11's request-shape audit fix is in main. Two-pass probe:
    # docs-private/research/2026-07-12-pin-final/reconcile_run{1,2}.json.
    assert _sio_wall_product_deposit_kg(1050.0) == pytest.approx(
        2.281305845076e-07, rel=1e-9
    )
    assert _sio_wall_product_deposit_kg(1400.0) == pytest.approx(
        2.429765790373e-07, rel=1e-9
    )
    assert _sio_wall_product_deposit_kg(1500.0) == pytest.approx(
        2.428777052747e-07, rel=1e-9
    )


def test_hot_wall_sio_reactive_deposit_uses_product_psat_floor():
    wall_T_K = 1700.0 + 273.15
    p_local_pa = 1.0
    alpha_s = 0.04

    sio_psat_pa = _antoine_psat_pa("SiO", wall_T_K)
    assert sio_psat_pa is not None and sio_psat_pa > p_local_pa

    expected_hkl = alpha_s * _hkl_impingement_flux_mol_m2_s(
        "SiO",
        p_local_pa,
        wall_T_K,
    )
    hkl_flux = _hkl_surface_deposition_flux_mol_m2_s(
        "SiO",
        P_local_pa=p_local_pa,
        T_surface_K=wall_T_K,
        alpha_s=alpha_s,
    )
    series_flux = _series_resistance_deposition_flux_mol_m2_s(
        "SiO",
        P_local_pa=p_local_pa,
        T_surface_K=wall_T_K,
        alpha_s=alpha_s,
        regime_factor=1.0,
    )

    assert hkl_flux == pytest.approx(expected_hkl)
    assert series_flux == pytest.approx(expected_hkl)
    assert hkl_flux > 0.0


@pytest.mark.parametrize(
    ("pressure_pa", "temperature_K"),
    [
        (float("nan"), 1700.0),
        (float("inf"), 1700.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
    ],
)
def test_hkl_impingement_flux_nonfinite_inputs_fail_closed(
    pressure_pa: float,
    temperature_K: float,
) -> None:
    assert _hkl_impingement_flux_mol_m2_s(
        "Na",
        pressure_pa,
        temperature_K,
    ) == pytest.approx(0.0)


def test_hot_wall_na_physisorber_reevaporates_against_own_psat():
    wall_T_K = 1700.0 + 273.15
    p_local_pa = 1.0

    na_psat_pa = _antoine_psat_pa("Na", wall_T_K)
    assert na_psat_pa is not None and na_psat_pa > p_local_pa

    assert _hkl_surface_deposition_flux_mol_m2_s(
        "Na",
        P_local_pa=p_local_pa,
        T_surface_K=wall_T_K,
        alpha_s=1.0,
    ) == 0.0
    assert _series_resistance_deposition_flux_mol_m2_s(
        "Na",
        P_local_pa=p_local_pa,
        T_surface_K=wall_T_K,
        alpha_s=1.0,
        regime_factor=1.0,
    ) == 0.0


def test_wall_deposition_reactivity_class_fails_loud(monkeypatch):
    with pytest.raises(ValueError, match="missing reactivity_class.*Unobtanium"):
        _sticking_reactivity_class("Unobtanium")

    monkeypatch.setitem(
        condensation_module.STICKING_DATA["reactivity_class_by_species"],
        "SiO",
        "chemisorbing",
    )
    with pytest.raises(ValueError, match="reactivity_class_by_species.SiO"):
        _hkl_surface_deposition_flux_mol_m2_s(
            "SiO",
            P_local_pa=100.0,
            T_surface_K=1000.0,
            alpha_s=1.0,
        )


def test_stage_scoped_no_reactive_backstop_skips_reactivity_metadata(monkeypatch):
    monkeypatch.delitem(
        condensation_module.STICKING_DATA["reactivity_class_by_species"],
        "SiO",
    )
    assert _hkl_surface_deposition_flux_mol_m2_s(
        "SiO",
        P_local_pa=1.0,
        T_surface_K=1700.0 + 273.15,
        alpha_s=1.0,
        reactive_product_backstop=False,
    ) == 0.0
