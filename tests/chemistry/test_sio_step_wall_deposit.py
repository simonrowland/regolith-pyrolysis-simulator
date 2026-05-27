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
    assert _sio_wall_deposit_kg(1050.0) == pytest.approx(
        6.589955385e-06, rel=1e-9
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
