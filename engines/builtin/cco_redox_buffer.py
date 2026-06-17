"""Pure CCO graphite-saturation redox-buffer math.

Stateless helper for the C-CO-CO2 buffer shared by future residual-carbon
melt-effect and oxygen-sink callers. Never mutates ledgers, builds transition
proposals, or imports melt/provider/inventory layers.

The certified point expression is the graphite-CO-CO2 (CCO) buffer of
Jakobsson & Oskarsson (1994, GCA), carried in the LEPR / ThermoEngine buffer
table; Stagno & Frost (2010, EPSL 300:72-84) use it as the CCO reference line in
their graphite-saturation work (they do not originate these point coefficients):

    log10(fO2 / bar) = -21803 / T_K + 4.325 + 0.171 * (P_bar - 1) / T_K

The expression is for graphite saturation; excess graphite does not add another
state variable. It is only a CCO reference line, not an EMOG/EMOD fit. Requests
that name the wider EMOG/EMOD graphite-saturation field return an interval so
callers cannot accidentally promote an uncurated coefficient choice to a
certified point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CCO_FORMULATION = "StagnoFrost2010_CCO_LEPR_graphite_CO_CO2"
CCO_SOURCE = (
    "Jakobsson & Oskarsson 1994 GCA (CCO point formula, via LEPR/ThermoEngine); "
    "Stagno & Frost 2010 EPSL 300:72-84 graphite-saturation context"
)
CCO_TEMPERATURE_RANGE_K = (1_273.15, 1_873.15)
QFM_REFERENCE_SOURCE = "O'Neill 1987 QFM fit used by MAGEMin adapter"

_CCO_A = -21_803.0
_CCO_B = 4.325
_CCO_C = 0.171
_QFM_A = -25_050.0
_QFM_B = 8.58


@dataclass(frozen=True)
class RedoxBufferPoint:
    log10_fO2_bar: float
    fO2_bar: float
    delta_log10_fO2_from_reference: float | None
    reference_buffer: str | None
    reference_log10_fO2_bar: float | None
    temperature_K: float
    pressure_bar: float
    formulation: str
    source: str
    validity: str


@dataclass(frozen=True)
class RedoxBufferInterval:
    low_log10_fO2_bar: float
    high_log10_fO2_bar: float
    certified_point: float | None
    reason: str
    formulation: str
    source: str


def _require_finite_positive(value: float, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


def _validity_for_temperature(temperature_K: float) -> str:
    lo, hi = CCO_TEMPERATURE_RANGE_K
    if lo <= temperature_K <= hi:
        return "inside_stagno_frost_cco_reference_range"
    return "outside_stagno_frost_cco_reference_range"


def qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill-style QFM log10(fO2/bar) used for buffer offsets."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    return _QFM_B + _QFM_A / temperature


def cco_log10_fO2_bar(temperature_K: float, pressure_bar: float = 1.0) -> float:
    """Return certified CCO graphite-CO-CO2 log10(fO2/bar)."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    return _CCO_A / temperature + _CCO_B + _CCO_C * (pressure - 1.0) / temperature


def cco_buffered_fO2(
    temperature_K: float,
    pressure_bar: float = 1.0,
    reference_buffer: str | None = "QFM",
) -> RedoxBufferPoint:
    """Return absolute CCO fO2 plus optional delta from a named reference buffer."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    log_fO2 = cco_log10_fO2_bar(temperature, pressure)

    reference_name: str | None
    reference_log: float | None
    delta_log: float | None
    if reference_buffer is None:
        reference_name = None
        reference_log = None
        delta_log = None
    else:
        reference_name = reference_buffer.upper()
        if reference_name not in {"QFM", "FMQ"}:
            raise ValueError(f"unsupported reference_buffer {reference_buffer!r}")
        reference_log = qfm_log10_fO2_bar(temperature)
        delta_log = log_fO2 - reference_log

    return RedoxBufferPoint(
        log10_fO2_bar=log_fO2,
        fO2_bar=10.0**log_fO2,
        delta_log10_fO2_from_reference=delta_log,
        reference_buffer=reference_name,
        reference_log10_fO2_bar=reference_log,
        temperature_K=temperature,
        pressure_bar=pressure,
        formulation=CCO_FORMULATION,
        source=CCO_SOURCE,
        validity=_validity_for_temperature(temperature),
    )


def cco_log10_fO2_interval_for_pressure_range(
    temperature_K: float,
    pressure_bar_range: tuple[float, float],
) -> RedoxBufferInterval:
    """Return an honest interval when overhead pressure is specified as a range."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    if len(pressure_bar_range) != 2:
        raise ValueError("pressure_bar_range must contain exactly two endpoints")
    p0 = _require_finite_positive(pressure_bar_range[0], "pressure_bar_range[0]")
    p1 = _require_finite_positive(pressure_bar_range[1], "pressure_bar_range[1]")
    low_p, high_p = sorted((p0, p1))
    low = cco_log10_fO2_bar(temperature, low_p)
    high = cco_log10_fO2_bar(temperature, high_p)
    return RedoxBufferInterval(
        low_log10_fO2_bar=min(low, high),
        high_log10_fO2_bar=max(low, high),
        certified_point=None,
        reason="pressure_range_not_certified_point",
        formulation=CCO_FORMULATION,
        source=CCO_SOURCE,
    )


def stagno_frost_emog_emod_log10_fO2_interval(
    low_log10_fO2_bar: float,
    high_log10_fO2_bar: float,
) -> RedoxBufferInterval:
    """Represent uncurated EMOG/EMOD graphite-saturation bounds as an interval."""
    low = float(low_log10_fO2_bar)
    high = float(high_log10_fO2_bar)
    if not math.isfinite(low) or not math.isfinite(high):
        raise ValueError("EMOG/EMOD interval endpoints must be finite")
    lo, hi = sorted((low, high))
    return RedoxBufferInterval(
        low_log10_fO2_bar=lo,
        high_log10_fO2_bar=hi,
        certified_point=None,
        reason="emog_emod_coefficients_not_certified_for_point_use",
        formulation="StagnoFrost2010_EMOG_EMOD_interval_only",
        source="Stagno & Frost 2010 EPSL 300:72-84",
    )
