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


def test_wall_band_capture_stays_bounded_after_reactive_sio_fix():
    capture_1050 = _stage3_silica_kg(1050.0) + _sio_wall_deposit_kg(1050.0)
    capture_1300 = _stage3_silica_kg(1300.0) + _sio_wall_deposit_kg(1300.0)
    capture_1400 = _stage3_silica_kg(1400.0) + _sio_wall_deposit_kg(1400.0)

    # Reactive SiO wall products no longer re-evaporate against SiO's own
    # Antoine curve, so hot-wall capture is nonzero and the old strict
    # cooler-wall monotone is no longer the invariant. Keep the capture band
    # tight enough to catch routing regressions without forcing a false zero.
    captures = (capture_1050, capture_1300, capture_1400)
    assert min(captures) > 0.0
    assert max(captures) - min(captures) <= 0.04 * max(captures)
    # Post-r7 autoreview fix (2026-05-27): equal-temperature wall routing
    # now restricts deposit candidates to reachable (upstream-of-designated-
    # stage) pipe segments. Pre-r7, downstream-of-stage-3 pipe segments
    # were spuriously credited with SiO wall deposits that physically
    # could not reach them — that ghost-mass was effectively absorbed by
    # the wall_deposit account when the honest accounting puts it into
    # `terminal_offgas_escape` (mass that flows past stage 3 to the
    # cold-vent line). The fix makes terminal_escape more honest at the
    # cold-liner end. At 1050 °C wall T the wall deposit drops ~57%
    # (1.5e-5 → 6.6e-6 kg, see test_wall_deposit_crosses_*) and the
    # released phantom-wall mass routes into terminal_escape, lifting
    # escape(1050) above escape(1300) by ~1.4e-7 kg (0.13% of total
    # escape ~1e-4 kg). The strict monotone `escape(1050) <= escape(1300)`
    # is no longer guaranteed at FP scale; relax to a 1%-of-largest
    # tolerance so the assertion still catches gross routing regressions
    # without false-failing on the honest physics correction.
    escape_1050 = _terminal_escape_kg(1050.0)
    escape_1300 = _terminal_escape_kg(1300.0)
    escape_1400 = _terminal_escape_kg(1400.0)
    band_tolerance_kg = 0.01 * max(escape_1050, escape_1300, escape_1400)
    assert escape_1050 <= escape_1300 + band_tolerance_kg
    assert escape_1300 <= escape_1400 + band_tolerance_kg
