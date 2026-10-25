"""SiO step isolation: CONDENSATION_ROUTE placement."""

from __future__ import annotations

import pytest

from functools import lru_cache
from typing import Any

from tests.studio_pin_support import sio_pin_values, sio_report_at_wall_temperature


@lru_cache(maxsize=None)
def _report_at_wall_T(liner_temperature_c: float) -> tuple[dict[str, Any], dict[str, float]]:
    return sio_report_at_wall_temperature(liner_temperature_c)


def _stage3_silica_kg(liner_temperature_c: float) -> float:
    report, _ = _report_at_wall_T(liner_temperature_c)
    return sio_pin_values(report)["sio_stage3_silica_kg"]


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


def test_subfloor_sio_materializes_declared_stage3_product():
    # Unavailable pure-SiO saturation continues the declared product route.
    assert _stage3_silica_kg(1400.0) > 0.0
    retained_mol = _retained_holdup_sio_mol(1400.0)
    assert retained_mol >= 0.0
    assert retained_mol + _terminal_escape_sio_mol(1400.0) > 0.0


def test_wall_band_extension_preserves_sio_throughput():
    capture_1050 = _captured_sio_equivalent_mol(1050.0)
    capture_1300 = _captured_sio_equivalent_mol(1300.0)
    capture_1400 = _captured_sio_equivalent_mol(1400.0)

    # Declared routing captures across the range; total throughput stays bounded.
    assert capture_1050 > 0.0
    assert capture_1300 > 0.0
    assert capture_1400 > 0.0
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
