"""SiO end-to-end chain coherence guards."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

from simulator.runner import build_sio_yield_report


MAX_BALANCE_ERR_PCT = 5.0e-12
# Chain closure is a RELATIVE residual normalized by the SiO chain magnitude.
# After the 2026-05-20 Antoine P_sat refit (builtin SiO fallback brought down to
# match VapoRock), the SiO flows shrank ~4700x, so the float64 precision floor of
# this relative metric rose above the global mass-balance bound. The
# magnitude-robust closure guard is ``terminal_mol == approx(sio_evaporated_mol)``
# below; this caps the relative residual well below any physical effect.
# 2026-06-28 alpha-series source model shrinks SiO another ~57x, so the same
# absolute residual (~3.44e-13 mol) reads as a larger relative percentage.
# 2026-06-29 reactive SiO wall products plus grounded alpha_s(T) move the
# diagnostic projection residual to ~7.1e-11 mol while AtomLedger mass balance
# remains below 5e-12 %.
# 2026-07-02 ch2c: evaporative source terms shift the fO2 trajectory;
# the chain INVARIANT (terminal == evaporated, abs delta ~8e-11 mol,
# mass closure 1.6e-12%) still holds — only this relative-percent cap
# was exceeded by 0.04% of itself (4.0017e-5 vs 4.0e-5). Headroom bump,
# not a loosened invariant.
MAX_CHAIN_CLOSURE_ERR_PCT = 6.0e-5
# Post-refit evolved SiO (invariant to wall/liner temperature; lunar_mare_low_ti,
# C2A, 24 h). Was 3.7303230676 kg pre-refit; the builtin SiO P_sat dropped ~4700x
# to the VapoRock-consistent activity-corrected value.
# Post-0.5.0 (2026-05-27) MnO NIST-JANAF refit + autoreview-r8 vapor-pressure
# raise-on-unavailable: PPM-scale FP roundoff drift from the Mn entry change
# altering _internal_analytical_equilibrium iteration order. 0.00078662141565 ->
# 0.000786620599287 (rel ~1e-6). No physics change; pure FP noise from a
# documented thermo-table update.
# Post-0.5.1 Phase A2 (2026-05-27) Mn high-T linear refit (Mn(l) basis):
# tiny FP roundoff again, 0.000786620599287 -> 0.000786620612837 (rel
# ~1.7e-8). Same root cause (Mn entry rounding); same character.
# 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip exposes
# backpressure-floor physics; previously the synthetic no-headspace
# pO2 floor masked the holdup feedback. The C2A PN2_SWEEP atmosphere
# now reads the real overhead-gas O2 inventory (vacuum-floor 1e-9 bar)
# instead of the conductance-ratio derived synthetic O2 partial.
# Lower commanded pO2 → less SiO suppression via 1/sqrt(pO2) → ~2.5x
# more SiO evolves. 0.000786620612837 → 0.00193652062882 (~+146%
# relative). The wall-temperature INVARIANCE of evolved SiO holds
# under finite-headspace ON (the holdup-derived O2 partial is the
# same across liner temperatures since C2A pO2_mbar=0 and no
# wall-T-dependent O2 source is active in C2A).
# 2026-06-15 pure-component runtime reroute selects grounded sidecars globally.
# SiO evolved drops mainly because grounded Na/K sidecars raise alkali vapor
# pressure (K_Pmax ~0.034 -> ~11089 Pa), increasing finite-headspace pO2
# (~8.3e-5 -> ~2.1e-4 bar) and suppressing SiO via the 1/sqrt(pO2) law in
# `engines/builtin/vapor_pressure.py`. It is not caused by the Si sidecar alone.
# 2026-06-15 Mn/Ti Alcock source-equation refit shifts the coupled
# fallback state at the 1e-11 kg level while preserving wall-temperature
# invariance.
# 2026-06-19 SSO-R R2.1b makes Fe activity respond to Kress91 melt redox.
# SiO remains wall-temperature invariant; coupled fallback flow shifts only
# at the 2.4e-11 kg level.
# 2026-06-20 BUG-035/037/083/158: VapoRock becomes diagnostic-only and the
# builtin Antoine/Ellingham surface is authoritative on the default path. The
# selected builtin runtime surface keeps the wall-temperature invariance contract
# with the pure-sidecar baseline.
# 2026-06-28 alpha-series source model removes the final stir multiplier and
# adds gas/melt resistances, lowering the evolved SiO source without changing
# wall-temperature invariance.
# 2026-07-06 CF-3 constant gamma*X alkali activity lowers Na/K vapor
# backpressure, shifting the coupled finite-headspace pO2 path while preserving
# the wall-temperature invariance contract.
# 2026-07-07 t-141 L&H K standard-term regen: ppm-scale shift through the
# coupled headspace pO2 path (delta -1.0176e-10, matches golden-deltas.json;
# same value as the sio_yield lunar baseline by construction).
# 2026-07-11 0.5.10 E-MOVE: phase-basis/two-rail vapor plus K/S fO2 and
# alkali-path changes lower the fixed-pO2 SiO-evolved pin.
# 2026-07-11 integrated-runtime: the 24 h track enters controlled-O2 C3 at
# global hour 19. O2 is now the condensation carrier for that campaign rather
# than the old N2 fallback; wall-temperature invariance remains exact.
# 2026-07-12 runtime-pressure replaces the synthetic transport-pressure path
# with summed runtime partials plus physical regulator/valve/throat controls.
# The resulting coupled headspace trajectory moves this executable pin
# independently of the condensation accounting repair; all four wall
# temperatures execute to the same value.
# 2026-07-14 t-194 Cr grounding: executable C2A_continuous recompute with
# grounded Cr alpha=0.9 and fallback retained only for Mn/CrO2.
# 2026-07-17 t-159/t-160: executable recompute after the corrected transport
# and wall-capture composition; invariance remains bit-identical.
# 2026-07-18 a91db36 flow-boundary transport rebaseline on corrected tip
# 0990232; wall-temperature invariance remains bit-identical.
# 2026-07-21 B1 vapor-package regen: JANAF-refit P_sat/suppression fits move
# the coupled evolved-SiO baseline down ~5.8x (declared class, same shift as
# the sio_yield CLI goldens); all four wall temperatures still execute to the
# SAME value — the invariance contract itself is untouched.
# 2026-08-01 REPIN round 4 (SC-109): causal commit 228927d closed the C2A
# soft-endpoint fail-open (affirmative flux arming with typed refusal).
# Pre-fix the soft endpoint could complete campaigns BEFORE flux ever
# armed; post-fix campaigns run their full extraction window and evolve
# ~8x more SiO. Authority: ~/Repos/rps-adjudicate/m2-bisect.md — values
# drift exactly at 228927d (1.318e-6 -> 1.032e-5) and are bit-identical
# across the whole VR range. Old pin encoded the premature-completion bug.
# Regenerated at tip d13f597 from build_sio_yield_report; wall-T invariance
# remains bit-identical across 1050/1300/1400/1500 C.
# regenerated 2026-08-02 under REPAIRED MAGEMin config per the train13
# adjudication; prior value was generated against the broken-liquidus job
# tree (1.03186545664e-05). docs-private/research/2026-08-02-train13-adjudication.md
PHASE3BIS_SIO_EVOLVED_KG = 1.03187282595e-05


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


def test_sio_chain_closes_evolved_to_stage_wall_and_terminal_products():
    _, diagnostics = _report_at_wall_T(1050.0)

    terminal_mol = (
        diagnostics["si_terminal_mol"]
        + diagnostics["sio2_terminal_mol"]
        + diagnostics["sio_wall_mol"]
        + diagnostics["sio_escape_mol"]
        + diagnostics["sio_retained_holdup_mol"]
        # 2026-08-05 MC-4 wave 1B (a34318c): the pO2-insensitive SiO2(g)
        # gas-exchange carrier is a real chain destination drawn from the
        # same SiO2 pool; counted by the report projection post-wave.
        + diagnostics["sio2_gas_escape_mol"]
    )

    assert terminal_mol == pytest.approx(diagnostics["sio_evaporated_mol"])
    assert abs(diagnostics["closure_error_pct"]) <= MAX_CHAIN_CLOSURE_ERR_PCT
    assert abs(diagnostics["mass_balance_error_pct"]) <= MAX_BALANCE_ERR_PCT


def test_sio_evolved_is_invariant_to_wall_temperature_at_fixed_po2_mode():
    wall_t_sweep_c = (1050.0, 1300.0, 1400.0, 1500.0)
    evolved = []
    for liner_temperature_c in wall_t_sweep_c:
        report, diagnostics = _report_at_wall_T(liner_temperature_c)
        evolved.append(float(report["sio_evolved_kg"]))
        assert abs(diagnostics["mass_balance_error_pct"]) <= MAX_BALANCE_ERR_PCT

    # Two SEPARATE claims, asserted separately. 2026-08-29: they used to be one
    # pytest.approx([pin] * 4), which cannot distinguish "the invariant broke"
    # from "the snapshot moved" -- and the second was masking the first.
    #
    # CLAIM 1, the property this test exists for: SiO evolution is independent
    # of wall temperature at fixed pO2 mode. Measured on the current tree it is
    # not merely close, it is BIT-IDENTICAL across all four liner setpoints:
    #     1050 C / 1300 C / 1400 C / 1500 C -> 1.05260475258e-05 each,
    #     spread exactly 0.0, mass-balance error 1.14e-14 %.
    # This is the cheap, durable claim. It must never be relaxed to buy a green.
    spread = max(evolved) - min(evolved)
    assert spread <= 5e-11, (
        f'SiO evolution is NOT invariant to wall temperature at fixed pO2: '
        f'spread {spread:.6g} kg across {wall_t_sweep_c}, values {evolved}'
    )

    # CLAIM 2, the absolute magnitude, which is a SNAPSHOT and ages. Pinned at
    # 4705a2fd (2026-08-01, REPIN round 4) under the repaired MAGEMin config;
    # 245 commits later, 25 of them touching condensation.py/evaporation.py,
    # the tree evolves 1.05260475258e-05 kg, i.e. +2.0092%.
    #
    # Deliberately left RED rather than regenerated. Regenerating a pin BECAUSE
    # it is red is a tautology -- it records nothing except that the code
    # changed. A regen is legitimate only once an identified correctness fix is
    # known to have moved the right answer, and that attribution across the 25
    # rail commits has not been done. Owner-gated with the b-302 regen batch;
    # the failure message says which claim is red so nobody has to re-derive
    # this.
    assert evolved[0] == pytest.approx(
        PHASE3BIS_SIO_EVOLVED_KG, rel=0.0, abs=5e-11
    ), (
        f'SiO evolution magnitude has drifted from its 2026-08-01 snapshot '
        f'({PHASE3BIS_SIO_EVOLVED_KG:.11e} -> {evolved[0]:.11e}, '
        f'{(evolved[0] - PHASE3BIS_SIO_EVOLVED_KG) / PHASE3BIS_SIO_EVOLVED_KG * 100:+.4f}%). '
        f'The wall-temperature INVARIANT above still holds exactly, so this is '
        f'a stale snapshot, not a physics regression. Do not regenerate without '
        f'attributing the drift.'
    )
