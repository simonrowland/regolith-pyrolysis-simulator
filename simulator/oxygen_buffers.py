"""Oxygen-fugacity buffer equations used by the simulator.

Graphite / C–CO oxygen fugacity for battery oxygen_condition.

Used when a paper states graphite (or C–CO) buffering and prints temperature
and P_CO (or total pressure when CO is the stated gas). The result is always
DERIVED — never a printed stamp.

Premise
-------
Graphite saturation with a CO-bearing C–O fluid. The elementary half-reaction
the papers name is

    C (graphite) + 1/2 O2 (g) = CO (g)

At graphite saturation the laboratory C–O fluid is the published graphite–
CO–CO2 (CCO) buffer, not the metastable pure-CO limiting case that omits
Boudouard CO2. The certified point expression is Jakobsson & Oskarsson
(1994, GCA), carried in LEPR / ThermoEngine and used as the CCO reference
line by Stagno & Frost (2010, EPSL 300:72-84):

Algebra
-------
For C + 1/2 O2 = CO,

    K = (P_CO / bar) / (fO2 / bar) ** (1/2)
    log10(fO2 / bar) = 2 * (log10(P_CO / bar) - log10(K))

The published CCO expression supplies the 1-atm reference value. At fixed T,
the reaction quotient therefore gives

    log10(fO2) = log10(fO2_ref) + 2 * log10(P_CO / P_ref)

where P_ref = 1 atm = 1.01325 bar.

Units
-----
T_K / T_K and pressure ratios are dimensionless; the constant terms are
already on a log10(fO2/bar) scale.

Sanity
------
At T = 1473.15 K and P_CO = 1.01325 bar (1 atm):
    log10(fO2/bar) = -10.475256413
This matches the published CCO line and the numeric values previously computed
for ts1985 via engines.builtin.cco_redox_buffer (same coefficients).
At the same T and P_CO = 0.101325 bar (0.1 atm):
    log10(fO2/bar) = -12.475256413
which is exactly two log units lower as required by the reaction quotient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.scalar_boundary import is_declared_real_scalar


class Kress91InvalidControls(ValueError):
    """Invalid finite-control input for the Kress91 Fe-redox relation."""


def oneill_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return the O'Neill QFM buffer log10(fO2/bar) at ``temperature_K``.

    Source: PySulfSat's linearisation (s6_corrections.py:640, labelled
    "Oneill"). This line is NOT printed in O'Neill, H.St.C. (1987), Am. Mineral.
    72, 67-75 (REF-035): that paper's p. 73 gives QFM (reaction 2) as an
    unnumbered least-squares fit valid over 900-1420 K, which this line tracks
    to about 0.03 log10 between 1000 and 1420 K. Above 1420 K both forms are
    extrapolated. The MAGEMin buffer translation that uses this line is
    tracked as b-913.
    T_K <= 0 is deliberately unguarded here; callers own their domain checks.
    """
    return 8.58 - 25050 / temperature_K


def _require_finite_positive(value: float, label: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


# Jakobsson & Oskarsson 1994 CCO point coefficients via LEPR / ThermoEngine.
_CCO_A = -21_803.0
_CCO_B = 4.325
_CCO_C = 0.171
_REFERENCE_PRESSURE_BAR = 1.01325
_CCO_EQUATION = (
    f"log10(fO2/bar) = {_CCO_A:g}/T_K + {_CCO_B:g} + "
    f"{_CCO_C:g}*(P_bar - 1)/T_K"
)

CCO_FORMULATION = "JakobssonOskarsson1994_CCO_via_LEPR_graphite_CO_CO2"
CCO_SOURCE = (
    "Jakobsson & Oskarsson 1994 GCA (CCO point formula, via LEPR/ThermoEngine); "
    "Stagno & Frost 2010 EPSL 300:72-84 graphite-saturation context"
)
CCO_TEMPERATURE_RANGE_K = (1_273.15, 1_873.15)
QFM_REFERENCE_SOURCE = "O'Neill 1987 QFM fit used by MAGEMin adapter"


def cco_log10_fO2_bar(temperature_K: float, pressure_bar: float = 1.0) -> float:
    """Return Jakobsson & Oskarsson CCO log10(fO2/bar) at T and pressure."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    return _CCO_A / temperature + _CCO_B + _CCO_C * (pressure - 1.0) / temperature


def log10_fo2_c_co_bar(temperature_K: float, p_co_bar: float) -> float:
    """Return log10(fO2/bar) for graphite / C–CO at T and P_CO.

    ``p_co_bar`` is the printed CO pressure in bar (or total pressure in bar
    when the paper states CO is the gas).
    """
    if not is_declared_real_scalar(temperature_K, allow_numeric_str=True):
        raise TypeError("temperature_K must be numeric")
    if not is_declared_real_scalar(p_co_bar, allow_numeric_str=True):
        raise TypeError("p_co_bar must be numeric")
    temperature = float(temperature_K)
    pressure = float(p_co_bar)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    if not math.isfinite(pressure) or pressure <= 0.0:
        raise ValueError("p_co_bar must be finite and positive")
    reference_log10_fo2 = cco_log10_fO2_bar(
        temperature,
        _REFERENCE_PRESSURE_BAR,
    )
    return reference_log10_fo2 + 2.0 * math.log10(
        pressure / _REFERENCE_PRESSURE_BAR
    )


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


def _validity_for_temperature(temperature_K: float) -> str:
    lo, hi = CCO_TEMPERATURE_RANGE_K
    if lo <= temperature_K <= hi:
        return "inside_cco_reference_range"
    return "outside_cco_reference_range"


def qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill-style QFM log10(fO2/bar) used for buffer offsets."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    return oneill_qfm_log10_fO2_bar(temperature)


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


def legacy_iw_log10_fO2_bar(
    temperature_K: float,
    *,
    vacuum_floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    """Return the legacy IW line, flooring T unless it is strictly positive."""
    temperature = float(temperature_K)
    if not (temperature > 0.0):
        return math.log10(vacuum_floor_bar)
    return -27215.0 / temperature + 6.57


# Holzheid et al. 1997, DOI 10.1016/S0009-2541(97)00030-2:
# Delta G("FeO"_l) = -244118 + 115.559*T - 8.474*T*ln(T) J/mol.
HOLZHEID_FEO_LIQUID_DG_A_J_MOL = -244118.0
HOLZHEID_FEO_LIQUID_DG_B_J_MOL_K = 115.559
HOLZHEID_FEO_LIQUID_DG_C_J_MOL_K = -8.474


def holzheid_feo_liquid_delta_g_j_mol(T_K: float) -> float:
    T = float(T_K)
    if T <= 0.0 or not math.isfinite(T):
        raise Kress91InvalidControls(
            f'FeO liquid DeltaG invalid control T_K: expected finite positive '
            f'value, got {T_K!r}'
        )
    return (
        HOLZHEID_FEO_LIQUID_DG_A_J_MOL
        + HOLZHEID_FEO_LIQUID_DG_B_J_MOL_K * T
        + HOLZHEID_FEO_LIQUID_DG_C_J_MOL_K * T * math.log(T)
    )


def feo_iw_log10_fO2_bar(T_K: float, *, a_feo: float = 1.0) -> float:
    activity = max(float(a_feo), 1.0e-300)
    R = 8.31446261815324
    ln_fO2 = 2.0 * (
        math.log(activity)
        + holzheid_feo_liquid_delta_g_j_mol(T_K) / (R * float(T_K))
    )
    return ln_fO2 / math.log(10.0)


# This identifier follows the coded line's wustite-liquid standard. Holzheid
# et al. (1997), Eq. 9, constructed it from O'Neill and Pownceby (1993) and
# Barin (1989); the paper states no uncertainty for the line, and this code
# states no validity range.
FE_FEO_BUFFER_STANDARD = 'wustite_FeO_l_holzheid1997_eq9'


FROST_QFM_ALPHA_COEFFICIENTS = (-26455.3, 10.344, 0.092)
FROST_QFM_BETA_COEFFICIENTS = (-25096.3, 8.735, 0.110)


def _qfm_logfo2_frost(T_K: float, P_bar: float) -> float:
    """Return absolute log10(fO2) of the Frost (1991) QFM buffer."""
    T_K = float(T_K)
    P_bar = float(P_bar)
    # Jugo et al. (2010) defines deltaQFM against Frost (1991), not O'Neill.
    # Frost's log10(fO2) = A + B/T + C(P-1)/T, as reproduced by PySulfSat
    # 1.0.12 (s6_corrections.py:643-653): beta quartz uses A=8.735,
    # B=-25096.3 K, C=0.110 K/bar; alpha quartz uses A=10.344,
    # B=-26455.3 K, C=0.092 K/bar. Thus every term is dimensionless
    # (log10 units). At 1200 K and 1 bar the beta branch gives -12.1786,
    # the published Frost QFM value and PySulfSat's result at that point.
    quartz_transition_K = 573.0 + 273.15 + 0.025 * P_bar
    if T_K < quartz_transition_K:
        return frost_condensed_buffer_log10_fO2_bar(
            *FROST_QFM_ALPHA_COEFFICIENTS, T_K, P_bar
        )
    return frost_condensed_buffer_log10_fO2_bar(
        *FROST_QFM_BETA_COEFFICIENTS, T_K, P_bar
    )


def frost_condensed_buffer_log10_fO2_bar(
    A_K: float | Decimal,
    B: float | Decimal,
    pressure_coefficient_K_per_bar: float | Decimal,
    T_K: float | Decimal,
    P_bar: float | Decimal,
) -> float | Decimal:
    # Premise: Frost (1991), doi:10.2138/rmg.1991.25.1, Table 1,
    # condensed buffer equilibrium inside its published T domain.
    # Algebra: log10(fO2/bar)=A/T+B+C*(P_bar-1)/T.
    # Units: K/K and (K/bar)*bar/K are dimensionless.
    # Sanity: IW at 1000 K, 1 bar gives -20.787.
    """Evaluate a Frost (1991) Table 1 condensed-buffer calibration."""
    return (
        A_K / T_K
        + B
        + pressure_coefficient_K_per_bar * (P_bar - 1) / T_K
    )
