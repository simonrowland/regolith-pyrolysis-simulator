"""SiO step isolation: CONDENSATION_ROUTE placement."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

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


def _stage3_silica_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return float(
        report["sio_to_silica_fume_kg"]["stage_3_sio_zone_product"]
    )


def _terminal_escape_sio_mol(liner_temperature_c: float) -> float:
    _, diagnostics = _report_at_wall_T(liner_temperature_c)
    return float(diagnostics["sio_escape_mol"])


def _captured_sio_equivalent_mol(liner_temperature_c: float) -> float:
    _, diagnostics = _report_at_wall_T(liner_temperature_c)
    return float(
        diagnostics["si_terminal_mol"]
        + diagnostics["sio2_terminal_mol"]
        + diagnostics["sio_wall_mol"]
    )


def test_sio_routes_to_stage3_for_c2a_after_band_aware_hk_fix():
    assert _stage3_silica_kg(1400.0) > 0.0


def test_wall_band_capture_stays_bounded_after_reactive_sio_fix():
    capture_1050 = _captured_sio_equivalent_mol(1050.0)
    capture_1300 = _captured_sio_equivalent_mol(1300.0)
    capture_1400 = _captured_sio_equivalent_mol(1400.0)

    # Count every Si-bearing terminal and reactive wall product on a common
    # SiO-mol basis. Direct wall SiO is now zero because the wall route emits
    # Si, SiO2, and FeSi; counting only SiO created the false 57% spread.
    captures = (capture_1050, capture_1300, capture_1400)
    assert min(captures) > 0.0
    assert max(captures) - min(captures) <= 0.04 * max(captures)
    # The report's presentation bucket named terminal_offgas_escape also adds
    # downstream collected SiO2, so it is not an escape-only invariant. Use the
    # ledger-derived SiO escape mol on the same basis as the capture check.
    escapes = tuple(
        _terminal_escape_sio_mol(temperature_C)
        for temperature_C in (1050.0, 1300.0, 1400.0)
    )
    assert max(escapes) - min(escapes) <= 0.01 * max(escapes)
