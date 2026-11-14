"""Oxygen-fugacity buffer equations used by the simulator.

Graphite / C–CO oxygen fugacity for battery ``oxygen_condition``. A stated CO
pressure uses the JANAF graphite-to-CO reaction; a closed graphite-saturated
CO–CO2 fluid uses its two JANAF formation equilibria outside the calibrated
Jakobsson–Oskarsson pressure/temperature field. Predictions remain DERIVED.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from decimal import Decimal

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.physical_constants import GAS_CONSTANT
from simulator.reference_data.janaf import _formation_gibbs_energy_kj_per_mol_at
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


# O'Neill (1987), Am. Mineral. 72, 67-75, reaction (2), QFM; primary EMF fit.
# Premise: μ°_O2 for 3 Fe2SiO4 + O2 = 2 Fe3O4 + 3 SiO2 is printed as
# μ_O2 (±200 J/mol) = -587474 + 1584.427 T - 203.3164 T ln(T) + 0.092710 T²
# (900 < T < 1420 K; the quoted ±200 J/mol is the uncertainty, not an offset;
# 1 bar). At equilibrium μ°_O2=RT ln(fO2/bar), so
# log10(fO2/bar)=μ°_O2/(R T ln(10)). The units cancel to a dimensionless
# base-10 logarithm. At 1200 K this gives -12.296398239 (rounded).
ONEILL_QFM_TEMPERATURE_RANGE_K = (900.0, 1420.0)
ONEILL_QFM_UNCERTAINTY_J_MOL = 200.0


def oneill_1987_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill (1987) primary EMF QFM relation at 1 bar."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    mu_o2 = (
        -587474.0
        + 1584.427 * temperature
        - 203.3164 * temperature * math.log(temperature)
        + 0.092710 * temperature**2
    )
    return mu_o2 / (_ONEILL_IW_R * temperature * _ONEILL_IW_LN10)


# O'Neill (1988), Am. Mineral. 73, 470-486, equation (11), Fe + "FeO".
# The source gives μ_O2 (±100 J mol^-1) in three temperature segments;
# ±100 J mol^-1 is the uncertainty, not an additive offset. μ°_O2 is ΔG° for
# 2 Fe + O2 = 2 "FeO" at the 1-bar reference. Thus
# Source equations (O'Neill, p. 477, eq. 11):
# μ_O2 (±100) = -605812 + 1366.718T - 182.7955T ln(T) + 0.10359T²
# (833<T<1042); -519357 + 59.427T + 8.9276T ln(T) (1042<T<1184);
# -551159 + 269.404T - 16.9484T ln(T) (1184<T<1450), all J mol^-1.
# log10(fO2/bar) = μ°_O2 / (R T ln(10)); J mol^-1 /(J mol^-1 K^-1 K)
# is dimensionless, and the 1-bar standard makes the numerical logarithm
# directly log10(fO2/bar). At 1000 K the printed first segment gives
# log10(fO2/bar) = -20.799980169 (rounded), a reducing IW value.
ONEILL_IW_TEMPERATURE_RANGES_K = (
    (833.0, 1042.0),
    (1042.0, 1184.0),
    (1184.0, 1450.0),
)
ONEILL_IW_UNCERTAINTY_J_MOL = 100.0
_ONEILL_IW_R = GAS_CONSTANT
_ONEILL_IW_LN10 = math.log(10.0)


def oneill_iw_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill (1988) equation (11) log10(fO2/bar) for IW.

    Outside the printed range the nearest end segment is extrapolated; callers
    retain that range and flag the prediction rather than clamping its value.
    The three source equations are transcribed below. The printed ±100 J/mol
    uncertainty is metadata and does not shift μ°.
    """
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    if temperature < 1042.0:
        mu_o2 = (
            -605812.0
            + 1366.718 * temperature
            - 182.7955 * temperature * math.log(temperature)
            + 0.10359 * temperature**2
        )
    elif temperature < 1184.0:
        mu_o2 = (
            -519357.0
            + 59.427 * temperature
            + 8.9276 * temperature * math.log(temperature)
        )
    else:
        mu_o2 = (
            -551159.0
            + 269.404 * temperature
            - 16.9484 * temperature * math.log(temperature)
        )
    return mu_o2 / (_ONEILL_IW_R * temperature * _ONEILL_IW_LN10)


ONEILL_POWNCEBY_1993_IW_TEMPERATURE_RANGES_K = (
    (833.0, 1042.0),
    (1042.0, 1184.0),
    (1184.0, 1644.0),
)
ONEILL_POWNCEBY_1993_IW_UNCERTAINTY_J_MOL = 150.0


def _oneill_pownceby_1993_iw_mu_o2_uncertainty_log10_dex(
    temperature_K: float,
) -> float:
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    # Premise: the printed Fe–"FeO" equation labels its μO2 uncertainty
    # ±150 J/mol.
    # Algebra: log10(fO2/bar) = μO2 / (R T ln 10), so the band is
    # ΔμO2 / (R T ln 10). Unit check: (J mol⁻¹) / ((J mol⁻¹ K⁻¹) K)
    # is dimensionless and therefore a base-10 log band in dex.
    # Sanity check: at 1586.4 K, 150 J/mol gives 0.004939 dex.
    return ONEILL_POWNCEBY_1993_IW_UNCERTAINTY_J_MOL / (
        _ONEILL_IW_R * temperature * _ONEILL_IW_LN10
    )


def oneill_pownceby_1993_iw_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill & Pownceby (1993) Fe–\"FeO\" log10(fO2/bar).

    The three recommended µO2 equations are printed in the abstract, in
    J/mol O2 with T in K and a 1 bar reference. The abstract reports an overall
    precision of ±100 J/mol (one standard deviation); the specific Fe–\"FeO\"
    equation is marked ±150 J/mol.
    """
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    if temperature < 1042.0:
        mu_o2 = (
            -605568.0
            + 1366.420 * temperature
            - 182.7955 * temperature * math.log(temperature)
            + 0.10359 * temperature**2
        )
    elif temperature < 1184.0:
        mu_o2 = (
            -519113.0
            + 59.129 * temperature
            + 8.9276 * temperature * math.log(temperature)
        )
    else:
        mu_o2 = (
            -550915.0
            + 269.106 * temperature
            - 16.9484 * temperature * math.log(temperature)
        )
    return mu_o2 / (_ONEILL_IW_R * temperature * _ONEILL_IW_LN10)


def _require_finite_positive(value: float, label: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


# Jakobsson & Oskarsson 1994 CCO fit, coefficients as cited by LEPR / ThermoEngine.
_CCO_A = -21_803.0
_CCO_B = 4.325
_CCO_C = 0.171

CCO_FORMULATION = "graphite_C_CO_CO2_JANAF_with_JakobssonOskarsson1994_in_band"
CCO_SOURCE = (
    "NIST-JANAF C-093 CO(g) and C-095 CO2(g) formation Gibbs energies; "
    "Jakobsson & Oskarsson 1994 GCA Table 6 fit inside its calibrated field"
)
CCO_TEMPERATURE_RANGE_K = (1_373.15, 1_773.15)
CCO_PRESSURE_RANGE_BAR = (7_500.0, 20_000.0)
CCO_OUT_OF_BAND_NOTICE = "cco_fit_out_of_band_ideal_gas_used"
CCO_IN_CALIBRATION_VALIDITY = "inside_cco_jakobsson_oskarsson_calibration"
QFM_REFERENCE_SOURCE = "O'Neill 1987 QFM fit used by MAGEMin adapter"


@dataclass(frozen=True)
class _CCOStatedPressureResult:
    log10_fO2_bar: float
    p_co2_bar: float


def _co_log10_equilibrium_constants(temperature_K: float) -> tuple[float, float]:
    dg_co = float(_formation_gibbs_energy_kj_per_mol_at("C-093", temperature_K))
    dg_co2 = float(_formation_gibbs_energy_kj_per_mol_at("C-095", temperature_K))
    denominator = GAS_CONSTANT * temperature_K * math.log(10.0)
    return -dg_co * 1000.0 / denominator, -dg_co2 * 1000.0 / denominator


def _stated_co_pressure_result(
    temperature_K: float, p_co_bar: float
) -> _CCOStatedPressureResult:
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(p_co_bar, "p_co_bar")
    log_k_co, log_k_co2 = _co_log10_equilibrium_constants(temperature)
    log_fO2 = 2.0 * (math.log10(pressure) - log_k_co)
    try:
        p_co2 = 10.0 ** (log_k_co2 + log_fO2)
    except OverflowError as exc:
        raise ValueError("implied p_CO2 is outside the finite bar range") from exc
    if not math.isfinite(p_co2) or p_co2 <= 0.0:
        raise ValueError("implied p_CO2 is outside the finite positive bar range")
    return _CCOStatedPressureResult(log_fO2, p_co2)


def _closed_co_log10_fO2_bar(temperature_K: float, pressure_bar: float) -> float:
    """Solve an ideal closed graphite-saturated CO + CO2 fluid.

    Put ``s = sqrt(fO2/bar)``. JANAF gives ``pCO/bar = K_CO*s`` and
    ``pCO2/bar = K_CO2*s²``. The total-pressure constraint is therefore
    ``K_CO2*s² + K_CO*s - P/bar = 0``; take its positive root and return
    ``2*log10(s)``. The rationalized root is
    ``s = 2(P/bar)/(sqrt(K_CO² + 4*K_CO2*(P/bar)) + K_CO)``. All K values and
    pressure ratios are dimensionless; P is bar and T is K. At 1673.15 K and
    P=1 bar, this gives log10(fO2/bar)=-16.1403418 and pCO2=1.70938e-4 bar.
    As pCO2 tends to zero, the root reduces to ``s=(P/bar)/K_CO``.
    """
    log_k_co, log_k_co2 = _co_log10_equilibrium_constants(temperature_K)
    k_co = 10.0**log_k_co
    k_co2 = 10.0**log_k_co2
    # The positive quadratic root is rationalized to avoid cancellation when
    # the CO2 term is negligible: s = 2P / (sqrt(K_CO² + 4K_CO2P) + K_CO).
    s = (2.0 * pressure_bar) / (
        math.sqrt(k_co**2 + 4.0 * k_co2 * pressure_bar) + k_co
    )
    return 2.0 * math.log10(s)


def _cco_fit_is_calibrated(temperature_K: float, pressure_bar: float) -> bool:
    return (
        CCO_TEMPERATURE_RANGE_K[0] <= temperature_K <= CCO_TEMPERATURE_RANGE_K[1]
        and CCO_PRESSURE_RANGE_BAR[0] <= pressure_bar <= CCO_PRESSURE_RANGE_BAR[1]
    )


def _cco_fit_out_of_band_validity() -> str:
    t_lo, t_hi = CCO_TEMPERATURE_RANGE_K
    p_lo, p_hi = CCO_PRESSURE_RANGE_BAR
    return (
        f"{CCO_OUT_OF_BAND_NOTICE}; T_K=[{t_lo:g},{t_hi:g}]; "
        f"P_bar=[{p_lo:g},{p_hi:g}]"
    )


def _cco_log10_fO2_and_validity(
    temperature_K: float, pressure_bar: float
) -> tuple[float, str]:
    if _cco_fit_is_calibrated(temperature_K, pressure_bar):
        return (
            _CCO_A / temperature_K
            + _CCO_B
            + _CCO_C * (pressure_bar - 1.0) / temperature_K,
            CCO_IN_CALIBRATION_VALIDITY,
        )
    return (
        _closed_co_log10_fO2_bar(temperature_K, pressure_bar),
        _cco_fit_out_of_band_validity(),
    )


def cco_log10_fO2_bar(temperature_K: float, pressure_bar: float = 1.0) -> float:
    """Return CCO log10(fO2/bar), warning when the ideal-gas fallback is used.

    The Jakobsson–Oskarsson (1994) fit applies only to its Table 6 field,
    1373.15–1773.15 K and 7,500–20,000 bar. Elsewhere this scalar API returns
    the closed ideal CO–CO2 result and emits ``cco_fit_out_of_band_ideal_gas_used``;
    callers needing a structured notice use :func:`cco_buffered_fO2`.
    """
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    log_fO2, validity = _cco_log10_fO2_and_validity(temperature, pressure)
    if validity != CCO_IN_CALIBRATION_VALIDITY:
        warnings.warn(validity, RuntimeWarning, stacklevel=2)
    return log_fO2


def log10_fo2_c_co_bar(temperature_K: float, p_co_bar: float) -> float:
    """Return log10(fO2/bar) for graphite / C–CO at T and P_CO.

    Premise: ``C(gr) + 1/2 O2(g) = CO(g)`` with graphite activity one. The
    JANAF C-093 formation Gibbs energy gives ``log10(K_CO) = -ΔfG°/(RT ln10)``;
    since ``K_CO = pCO/sqrt(fO2)``, ``log10(fO2/bar) =
    2(log10(pCO/bar) - log10(K_CO))``. For the diagnostic, JANAF C-095 gives
    ``K_CO2 = pCO2/fO2`` and thus ``pCO2 = K_CO2*fO2``. Units are bar for
    pressures, K for temperature, and J mol⁻¹ for ``R*T``. At 1673.15 K and
    pCO=1 bar, log10(fO2/bar) = -16.1401933 and implied pCO2 is
    1.70997e-4 bar. A tenfold pCO reduction lowers log10(fO2) by exactly 2.

    ``p_co_bar`` is the stated CO partial pressure in bar (or total pressure
    when the source states CO is the gas); CO2 remains a reported diagnostic.
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
    return _stated_co_pressure_result(temperature, pressure).log10_fO2_bar


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

    @property
    def inside_valid_range(self) -> bool:
        return self.validity == CCO_IN_CALIBRATION_VALIDITY

    @property
    def notice(self) -> str | None:
        return None if self.inside_valid_range else self.validity


@dataclass(frozen=True)
class RedoxBufferInterval:
    low_log10_fO2_bar: float
    high_log10_fO2_bar: float
    certified_point: float | None
    reason: str
    formulation: str
    source: str


def qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill-style QFM log10(fO2/bar) used for buffer offsets."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    return oneill_qfm_log10_fO2_bar(temperature)


def cco_buffered_fO2(
    temperature_K: float,
    pressure_bar: float = 1.0,
    reference_buffer: str | None = "QFM",
) -> RedoxBufferPoint:
    """Return CCO fO2 and a structured in-band or fallback validity notice."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    log_fO2, validity = _cco_log10_fO2_and_validity(temperature, pressure)

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
        validity=validity,
    )


def cco_log10_fO2_interval_for_pressure_range(
    temperature_K: float,
    pressure_bar_range: tuple[float, float],
) -> RedoxBufferInterval:
    """Enclose the owner model over a pressure range at fixed temperature.

    Evaluate both requested endpoints and each crossed model boundary. At a
    boundary coincident with an interval endpoint, include only the one-sided
    limit facing into the interval; a degenerate interval contains only its
    point evaluation. Each one-sided limit is evaluated with its owning branch's
    formula at the exact boundary pressure, not by shifting pressure with
    ``nextafter``. The fit branch applies only inside its calibrated T range.
    """
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    if len(pressure_bar_range) != 2:
        raise ValueError("pressure_bar_range must contain exactly two endpoints")
    p0 = _require_finite_positive(pressure_bar_range[0], "pressure_bar_range[0]")
    p1 = _require_finite_positive(pressure_bar_range[1], "pressure_bar_range[1]")
    low_p, high_p = sorted((p0, p1))
    low, low_validity = _cco_log10_fO2_and_validity(temperature, low_p)
    high, high_validity = _cco_log10_fO2_and_validity(temperature, high_p)
    values = [low, high]
    branches = {
        CCO_IN_CALIBRATION_VALIDITY
        if validity == CCO_IN_CALIBRATION_VALIDITY
        else "ideal_gas_fallback"
        for validity in (low_validity, high_validity)
    }
    temperature_in_fit_band = (
        CCO_TEMPERATURE_RANGE_K[0]
        <= temperature
        <= CCO_TEMPERATURE_RANGE_K[1]
    )
    for boundary_pressure in CCO_PRESSURE_RANGE_BAR:
        if not temperature_in_fit_band:
            continue

        if low_p < boundary_pressure < high_p:
            interval_sides = ("below", "above")
        elif low_p == boundary_pressure and low_p < high_p:
            interval_sides = ("above",)
        elif high_p == boundary_pressure and low_p < high_p:
            interval_sides = ("below",)
        else:
            continue

        for side in interval_sides:
            fit_side = (
                CCO_PRESSURE_RANGE_BAR[0] < boundary_pressure
                if side == "below"
                else boundary_pressure < CCO_PRESSURE_RANGE_BAR[1]
            )
            if fit_side:
                boundary_value, validity = _cco_log10_fO2_and_validity(
                    temperature, boundary_pressure
                )
                branches.add(validity)
            else:
                boundary_value = _closed_co_log10_fO2_bar(
                    temperature, boundary_pressure
                )
                branches.add("ideal_gas_fallback")
            values.append(boundary_value)

    reason = "pressure_range_not_certified_point"
    if "ideal_gas_fallback" in branches:
        reason = f"{reason}; {_cco_fit_out_of_band_validity()}"
    branch_summary = ", ".join(sorted(branches))
    if len(branches) > 1:
        reason = f"{reason}; mixed branches=[{branch_summary}]"
    else:
        reason = f"{reason}; branch=[{branch_summary}]"
    return RedoxBufferInterval(
        low_log10_fO2_bar=min(values),
        high_log10_fO2_bar=max(values),
        certified_point=None,
        reason=reason,
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
    """Return the legacy IW line used by runtime Fe-redox.

    This line is uncited and slated for removal per d-324; changing its
    runtime calibration is a separate owner card. Battery migration must use
    the cited O'Neill calibration below instead.
    """
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


def metal_oxide_coexistence_log10_fO2_bar(
    formation_gibbs_j_mol: float,
    oxygen_atoms: float,
    temperature_K: float,
) -> float:
    """log10(fO2/bar) for metal/oxide coexistence from the oxide formation Gibbs.

    Premise: the condensed metal is the formation reference, so ΔfG(M) = 0
    and the oxide formation reaction per mole of oxide is
    M + (n_O/2) O2 = MOn. Written per mole of O2 that is
    (2/n_O) M + O2 = (2/n_O) MOn, with ΔrG = (2/n_O) ΔfG.
    Algebra: log10(fO2/bar) = ΔrG / (R T ln 10)
    = 2 ΔfG / (n_O R T ln 10). The factor 2 is that normalization.
    It is not applied again.
    Unit check: J/mol / ((J/(mol·K))·K) is dimensionless.
    Sanity: ΔfG = −500000 J/mol, n_O = 2, T = 1800 K is the hex
    −0x1.d04c8123c6560p+3. Holzheid liquid FeO is a different expression
    and is not this function.
    """

    return (
        2.0
        * formation_gibbs_j_mol
        / (
            oxygen_atoms
            * GAS_CONSTANT
            * temperature_K
            * math.log(10.0)
        )
    )


def feo_iw_log10_fO2_bar(T_K: float, *, a_feo: float = 1.0) -> float:
    """Return the Holzheid activity-bearing IW relation for a_FeO input."""
    activity = max(float(a_feo), 1.0e-300)
    ln_fO2 = 2.0 * (
        math.log(activity)
        + holzheid_feo_liquid_delta_g_j_mol(T_K) / (GAS_CONSTANT * float(T_K))
    )
    return ln_fO2 / math.log(10.0)


def delta_iw_log10(fO2_log: float, iw_log: float) -> float:
    """log10(fO2/bar) minus the caller's IW log10(fO2/bar).

    Premise: ΔIW compares two fugacities already written as log10 of bar.
    This function does not choose the IW line. Legacy IW, Holzheid liquid
    FeO, and O'Neill IW are different numbers; the caller passes the one
    it means.
    Algebra: float(fO2_log) - float(iw_log).
    Unit check: both arguments are log10(bar), so the difference is
    dimensionless dex.
    Sanity: -10.0 - -12.5 is 2.5, hex 0x1.4p+1. The Decimal battery-hero
    ΔIW is a different subtraction and is not this function.
    """

    return float(fO2_log) - float(iw_log)


# This identifier follows the coded line's wustite-liquid standard. Holzheid
# et al. (1997), Eq. 9, constructed it from O'Neill and Pownceby (1993) and
# Barin (1989); the paper states no uncertainty for the line, and this code
# states no validity range.
FE_FEO_BUFFER_STANDARD = 'wustite_FeO_l_holzheid1997_eq9'


# Frost (1991), Reviews in Mineralogy 25, Table 1, printed page 5
# (PDF page 19 of frost-1991-oxygen-fugacity-rimg). The table writes
# FMQ as 2 Fe3O4 + 3 SiO2 = 3 Fe2SiO4 + O2, and
# "where log fO2 = A/T + B + C(P-1)/T (T in Kelvins)".
# FMαQ A=-26455.3 B=10.344 C=.092, 400–573 °C.
# FMβQ A=-25096.3 B=8.735 C=.110, 573–1200 °C.
# IW A=-27489 B=6.702 C=.055, 565–1200 °C.
# NiNiO A=-24930 B=9.36 C=.046, 600–1200 °C.
# WM A=-32807 B=13.012 C=.083, 565–1200 °C.
# "temperature of the alpha - beta quartz transition is approximated by
# the expression: T(°C) = 573 + 0.025P(bars)."
# "The buffers may be strongly curved at low pressures and it is advised
# not to extrapolate these expressions below the limits indicated."
# These coefficients are the central pressure term where P >> 1 bar.
#
# Chou (1978), American Mineralogist 63, is band-only, not central.
# The footnote on the page that ends at printed p. 695 (pdftotext of
# chou-1978-oxygen-buffer-calibration-hydrogen-sensor) reads, with the
# formula glyphs restored from the surrounding sentences:
# "log (fO2)_P,T = log (fO2)_1,T + (ΔVs / 2.303 R)·(P − 1)/T
# where ΔVs is the volume change of the reaction for solids. The molar
# volume data for solids are from Robie and Waldbaum (1968), and the
# coefficients for the pressure correction term for various buffers are
# given in Table 6."
# Table 6 (printed p. 700) FMQ row: A=-23973 (±165), B=7.985 (±0.172),
# C=0.092, source (1). Footnote: "C = −ΔVs/2.303 R" and "molar volume
# data for solids are from Robie and Waldbaum (1968)."
# Equation (22) is 2 Fe3O4 + 3 SiO2 = 3 Fe2SiO4 + O2, and equation (24)
# is log(fO2)_1,T (±0.08) = (−23973/T) + 7.985. The abstract states the
# sensor data, including FMQ, were taken "at 2 and 4 kbar total
# pressures between 600 and 800 C", and the abstract's FMQ line is
# "log fO2 (±0.08) = (−23973/T) + 7.985 for T > 873". Table 6 itself
# prints no separate FMQ temperature window on the C=0.092 row.
# The spread |0.110 − 0.092|·|P−1|/T is added to the QFM band and flagged.
# Chou's 0.092 is not added to the central log.
#
# Molar-volume sanity, oxidation form 3 Fa + O2 = 2 Mt + 3 Qz.
# Robie & Waldbaum (1968), USGS Bulletin 1259, 298.15 K, cm³/mol:
# fayalite 46.39, magnetite 44.524, α-quartz 22.688
# (data/literature records b1259-298k-0295-fayalite, -0148-magnetite,
# -0182-a-quartz). O2 is the 1 bar ideal-gas standard, so only solids
# enter ΔV.
# ΔV_solids = 2·44.524 + 3·22.688 − 3·46.39 = +17.942 cm³/mol
#           = +1.7942 J/bar/mol  (1 cm³ = 0.1 J/bar).
# The oxidized assemblage is larger, so fO2 rises with P. The sign is
# positive: d log10(fO2)/dP = +ΔV_solids / (R T ln 10).
# Equivalently, for Frost's reduction writing (ΔV = −17.942 cm³/mol),
# d log10(fO2)/dP = −ΔV_reduction / (R T ln 10), the same positive slope.
# C = ΔV / (R ln 10) = 1.7942 / (8.314462618 · ln 10) = 0.0937 K/bar.
# That 298 K α-quartz value sits on Frost's printed α C=0.092 and on
# Chou's C=0.092, not on Frost's β-quartz C=0.110.
FROST_IW_COEFFICIENTS = (-27489.0, 6.702, 0.055)
FROST_NNO_COEFFICIENTS = (-24930.0, 9.36, 0.046)
FROST_WM_COEFFICIENTS = (-32807.0, 13.012, 0.083)
FROST_QFM_ALPHA_COEFFICIENTS = (-26455.3, 10.344, 0.092)
FROST_QFM_BETA_COEFFICIENTS = (-25096.3, 8.735, 0.110)
FROST_BUFFER_TEMPERATURE_RANGES_K = {
    "IW": (838.15, 1473.15),
    "NNO": (873.15, 1473.15),
    "QFM": (846.15, 1473.15),
    "WM": (838.15, 1473.15),
}


def _qfm_logfo2_frost(T_K: float, P_bar: float) -> float:
    """Return absolute log10(fO2) of the Frost (1991) QFM buffer."""
    T_K = float(T_K)
    P_bar = float(P_bar)
    # SulfSat passes this Frost QFM curve to PySulfSat's Jugo Eq. 10 helper;
    # that adapter model is separate from canonical extract calibration.
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


def frost_buffer_log10_fO2_bar(
    buffer: str, T_K: float, P_bar: float = 1.0
) -> float:
    """Return the Frost (1991) log10(fO2/bar) for a named buffer."""
    name = buffer.upper()
    if name == "QFM":
        return _qfm_logfo2_frost(T_K, P_bar)
    coefficients = {
        "IW": FROST_IW_COEFFICIENTS,
        "NNO": FROST_NNO_COEFFICIENTS,
        "WM": FROST_WM_COEFFICIENTS,
    }.get(name)
    if coefficients is None:
        raise ValueError(f"unsupported Frost buffer {buffer!r}")
    return float(frost_condensed_buffer_log10_fO2_bar(*coefficients, T_K, P_bar))
