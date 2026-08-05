"""SiO step isolation: CONDENSATION_ROUTE placement."""

from __future__ import annotations

import pytest

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
        # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
        allow_unmeasured_alpha_fallback=True,
    )


def _stage3_silica_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return float(
        report["sio_to_silica_fume_kg"]["stage_3_sio_zone_product"]
    )


def _terminal_escape_sio_mol(liner_temperature_c: float) -> float:
    _, diagnostics = _report_at_wall_T(liner_temperature_c)
    return float(diagnostics["sio_escape_mol"])


def _retained_holdup_sio_mol(liner_temperature_c: float) -> float:
    _, diagnostics = _report_at_wall_T(liner_temperature_c)
    return float(diagnostics["sio_retained_holdup_mol"])


def _captured_sio_equivalent_mol(liner_temperature_c: float) -> float:
    _, diagnostics = _report_at_wall_T(liner_temperature_c)
    return float(
        diagnostics["si_terminal_mol"]
        + diagnostics["sio2_terminal_mol"]
        + diagnostics["sio_wall_mol"]
    )


def test_subfloor_sio_does_not_create_unmaterialized_stage3_product():
    # b-127: no Antoine segment covers this condenser temperature. The stage
    # therefore refuses capture instead of fabricating a saturation pressure;
    # evolved SiO remains in the gas train and cannot become Stage-3 product.
    assert _stage3_silica_kg(1400.0) == 0.0
    retained_mol = _retained_holdup_sio_mol(1400.0)
    assert retained_mol >= 0.0
    assert retained_mol + _terminal_escape_sio_mol(1400.0) > 0.0


def test_wall_band_refusal_preserves_sio_throughput():
    capture_1050 = _captured_sio_equivalent_mol(1050.0)
    capture_1300 = _captured_sio_equivalent_mol(1300.0)
    capture_1400 = _captured_sio_equivalent_mol(1400.0)

    # The covered 1050 C interval captures; uncovered 1300/1400 C intervals
    # refuse capture. Total captured + escaped SiO-equivalent stays bounded,
    # proving the refusal passes vapor onward instead of returning it to melt.
    assert capture_1050 > 0.0
    assert capture_1300 == 0.0
    assert capture_1400 == 0.0
    # The report's presentation bucket named terminal_offgas_escape also adds
    # downstream collected SiO2, so it is not an escape-only invariant. Use the
    # ledger-derived SiO escape mol on the same basis as the capture check.
    escapes = tuple(
        _terminal_escape_sio_mol(temperature_C)
        for temperature_C in (1050.0, 1300.0, 1400.0)
    )
    totals = tuple(
        capture + escape for capture, escape in zip(
            (capture_1050, capture_1300, capture_1400), escapes
        )
    )
    assert min(totals) > 0.0
    assert max(totals) - min(totals) <= 0.04 * max(totals)
