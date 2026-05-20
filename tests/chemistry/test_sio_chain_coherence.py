"""SiO end-to-end chain coherence guards."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

from simulator.runner import build_sio_yield_report


MAX_BALANCE_ERR_PCT = 5.0e-12
PHASE3BIS_SIO_EVOLVED_KG = 3.73034175962


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
    assert abs(diagnostics["closure_error_pct"]) <= MAX_BALANCE_ERR_PCT
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
