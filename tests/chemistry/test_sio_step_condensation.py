"""SiO step isolation: CONDENSATION_ROUTE placement."""

from __future__ import annotations

import math

import pytest

from functools import lru_cache
from typing import Any

import simulator.condensation as condensation
from simulator.state import MOLAR_MASS
from tests.studio_pin_support import sio_pin_values, sio_report_at_wall_temperature

# (available_kg, ntu) for each Stage-3 SiO call that evaluated the capture
# exponential. Filled by the one cached report, so the sibling wall-band
# test does not build a second 24 h report.
_stage3_sio_capture: dict[float, tuple[tuple[float, float], ...]] = {}


def _record_stage3_sio_capture(
    liner_temperature_c: float,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Run the wall report once and keep the Stage-3 SiO exp arguments.

    ``math.exp`` during ``_condensation_efficiency`` also evaluates the
    saturation expressions. Those arguments are only a few nats and are
    the same at every hour. The capture argument is the most negative one
    in the call (NTU = -min(negative args)). The wrapper returns the
    production eta unchanged.
    """
    calls: list[tuple[float, float]] = []
    model = condensation.CondensationModel
    original = model._condensation_efficiency
    real_exp = condensation.math.exp

    def wrapped(self, *args, **kwargs):
        species = kwargs.get("species")
        stage = kwargs.get("stage")
        exp_args: list[float] = []

        def logging_exp(value):
            number = float(value)
            exp_args.append(number)
            return real_exp(value)

        condensation.math.exp = logging_exp
        try:
            eta = original(self, *args, **kwargs)
        finally:
            condensation.math.exp = real_exp
        if species == "SiO" and getattr(stage, "stage_number", None) == 3:
            negative = [arg for arg in exp_args if arg < 0.0]
            if negative:
                calls.append((float(kwargs["available_kg"]), -min(negative)))
        return eta

    model._condensation_efficiency = wrapped
    try:
        report, diagnostics = sio_report_at_wall_temperature(liner_temperature_c)
    finally:
        model._condensation_efficiency = original
        condensation.math.exp = real_exp
    _stage3_sio_capture[liner_temperature_c] = tuple(calls)
    return report, diagnostics


@lru_cache(maxsize=None)
def _report_at_wall_T(liner_temperature_c: float) -> tuple[dict[str, Any], dict[str, float]]:
    return _record_stage3_sio_capture(liner_temperature_c)


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


def test_subfloor_sio_purity_veto_bypasses_stage3_product():
    assert _stage3_silica_kg(1400.0) == 0.0
    retained_mol = _retained_holdup_sio_mol(1400.0)
    escape_mol = _terminal_escape_sio_mol(1400.0)
    assert retained_mol >= 0.0
    assert escape_mol >= 0.0
    # The veto still keeps this SiO out of the stage-3 product. Cold stages
    # condense that vapour, so the parcel is capture, not escape.
    assert (
        retained_mol
        + escape_mol
        + _captured_sio_equivalent_mol(1400.0)
    ) > 0.0
    # Float64 rounds 1 - exp(-NTU) to 1.0 once NTU is past ~37, while
    # math.exp(-NTU) is still nonzero (it underflows only near NTU 709).
    # Escape stays within the smallest Stage-3 NTU this run evaluated.
    # NTU and available_kg come from that call, not a pin.
    observed = _stage3_sio_capture[1400.0]
    assert observed, "stage-3 SiO never evaluated the capture exponential"
    molar_mass_kg = MOLAR_MASS["SiO"] / 1000.0
    available_mol = sum(available_kg / molar_mass_kg for available_kg, _ntu in observed)
    min_ntu = min(ntu for _available_kg, ntu in observed)
    assert min_ntu > 0.0
    assert escape_mol <= available_mol * math.exp(-min_ntu)


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
