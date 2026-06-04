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
MAX_CHAIN_CLOSURE_ERR_PCT = 5.0e-8
# Post-refit evolved SiO (invariant to wall/liner temperature; lunar_mare_low_ti,
# C2A, 24 h). Was 3.7303230676 kg pre-refit; the builtin SiO P_sat dropped ~4700x
# to the VapoRock-consistent activity-corrected value.
# Post-0.5.0 (2026-05-27) MnO NIST-JANAF refit + autoreview-r8 vapor-pressure
# raise-on-unavailable: PPM-scale FP roundoff drift from the Mn entry change
# altering _stub_equilibrium iteration order. 0.00078662141565 ->
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
PHASE3BIS_SIO_EVOLVED_KG = 8.53253498332e-05


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


def test_sio_chain_closes_evolved_to_stage_wall_and_terminal_products():
    _, diagnostics = _report_at_wall_T(1050.0)

    terminal_mol = (
        diagnostics["si_terminal_mol"]
        + diagnostics["sio2_terminal_mol"]
        + diagnostics["sio_wall_mol"]
        + diagnostics["sio_escape_mol"]
    )

    assert terminal_mol == pytest.approx(diagnostics["sio_evaporated_mol"])
    assert abs(diagnostics["closure_error_pct"]) <= MAX_CHAIN_CLOSURE_ERR_PCT
    assert abs(diagnostics["mass_balance_error_pct"]) <= MAX_BALANCE_ERR_PCT


def test_sio_evolved_is_invariant_to_wall_temperature_at_fixed_po2_mode():
    evolved = []
    for liner_temperature_c in (1050.0, 1300.0, 1400.0, 1500.0):
        report, diagnostics = _report_at_wall_T(liner_temperature_c)
        evolved.append(float(report["sio_evolved_kg"]))
        assert abs(diagnostics["mass_balance_error_pct"]) <= MAX_BALANCE_ERR_PCT

    assert evolved == pytest.approx(
        [PHASE3BIS_SIO_EVOLVED_KG] * len(evolved), rel=0.0, abs=1e-11
    )
