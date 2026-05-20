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


def _terminal_escape_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return float(report["sio_to_silica_fume_kg"]["terminal_offgas_escape"])


def _sio_wall_deposit_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return float(report["wall_deposit_kg"].get("SiO", 0.0))


def test_sio_routes_to_stage3_for_c2a_after_band_aware_hk_fix():
    assert _stage3_silica_kg(1400.0) > 0.0


def test_cooler_wall_band_monotonically_increases_capture_and_reduces_escape():
    capture_1050 = _stage3_silica_kg(1050.0) + _sio_wall_deposit_kg(1050.0)
    capture_1300 = _stage3_silica_kg(1300.0) + _sio_wall_deposit_kg(1300.0)
    capture_1400 = _stage3_silica_kg(1400.0) + _sio_wall_deposit_kg(1400.0)

    assert capture_1050 > capture_1300 > capture_1400
    assert _terminal_escape_kg(1050.0) <= _terminal_escape_kg(1300.0)
    assert _terminal_escape_kg(1300.0) <= _terminal_escape_kg(1400.0)
