from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import yaml

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.feedstock_composition import (
    _representative_number,
    IronSeedAuthority,
    feot_equivalent_wt_pct,
    iron_oxide_values,
    normalize_component_masses_kg,
    normalized_feedstock_component_masses_kg,
    validate_iron_speciation,
)
from simulator.scalar_boundary import is_declared_real_scalar


class Kress91InvalidControls(ValueError):
    """Invalid finite-control input for the Kress91 Fe-redox relation."""


def intrinsic_melt_fO2(
    composition_wt_pct: Mapping[str, float],
    temperature_K: float,
    *,
    vacuum_floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    """Composition-only diagnostic using the same inverse as the ledger path."""
    from simulator.state import MOLAR_MASS

    feo, fe2o3 = iron_oxide_values(composition_wt_pct)
    return float(kress91_intrinsic_state(
        feo_mol=feo / MOLAR_MASS['FeO'],
        fe2o3_mol=fe2o3 / MOLAR_MASS['Fe2O3'],
        mol_fractions=melt_mol_fractions_for_kress91(composition_wt_pct),
        T_K=temperature_K, pressure_bar=vacuum_floor_bar,
        exchange_fO2_log=math.log10(vacuum_floor_bar),
        composition_wt_pct=composition_wt_pct,
    )['fO2_log'])


KRESS91_MOL_FRACTION_OXIDES = (
    'SiO2',
    'TiO2',
    'Al2O3',
    'MnO',
    'MgO',
    'CaO',
    'Na2O',
    'K2O',
    'P2O5',
)
# provenance: Kress91 coefficients — REF-001 CMP 108:82-92; ln(fO2) and inverse-T terms.
KRESS91_LN_FO2_COEFFICIENT = 0.196
KRESS91_INV_T_COEFFICIENT_K = 11492.0
KRESS91_NONLINEAR_REFERENCE_T_K = 1673.0
KRESS91_NONLINEAR_COEFFICIENT = -3.36
KRESS91_PRESSURE_INV_T_COEFFICIENT = -0.000000701
KRESS91_PRESSURE_D_T_COEFFICIENT = -0.000000000154
KRESS91_PRESSURE_SQUARED_COEFFICIENT = 0.0000000000000000385
# Kress91 liquid calibration floor. Kress91 coefficients above remain the
# thermodynamic source; higher-temperature bands are flagged, not model-swapped.
KRESS91_LIQUID_CALIBRATION_MIN_T_C = 1200.0
KRESS91_LIQUID_CALIBRATION_MAX_T_C = 1630.0
KRESS91_AITHALA_EXPERIMENTAL_CONFIRMATION_MAX_T_C = 2100.0
KRESS91_HIGH_UNCERTAINTY_MAX_T_C = 2500.0
# 1400 C cache-label convention for isochemical redox keys, not new physics.
KRESS91_FO2_KEY_REFERENCE_T_K = 1673.15
REDOX_STATE_REFERENCE_T_K = KRESS91_FO2_KEY_REFERENCE_T_K


def kress91_temperature_band_case(temperature_C: float) -> dict[str, object]:
    """Classify Kress91 temperature authority/extrapolation bands."""

    T_C = float(temperature_C)
    if not math.isfinite(T_C):
        return {
            'case': 'non_finite_temperature',
            'status': 'refused',
            'source': 'none:invalid_temperature',
            'authoritative': False,
            'extrapolation': False,
            'high_uncertainty': True,
        }
    # CASE liquidus..1200 C: extrapolation flagged below calibration floor;
    # source REF-001 defines the Kress91 liquid relation and 1200 C floor.
    if T_C < KRESS91_LIQUID_CALIBRATION_MIN_T_C:
        return {
            'case': 'below_1200C_extrapolation',
            'status': 'extrapolation_below_calibration_floor',
            'source': (
                'REF-001 Kress91 liquid relation; below 1200 C calibration floor'
            ),
            'authoritative': False,
            'extrapolation': True,
            'high_uncertainty': True,
        }
    # CASE 1200-1630 C: AUTHORITATIVE Kress & Carmichael 1991, REF-001,
    # doi:10.1007/BF00307328. Kilinc 1983 (REF-054) and Jayasuriya 2004
    # (REF-055) are comparison/validation sources, not wider model authority.
    if T_C <= KRESS91_LIQUID_CALIBRATION_MAX_T_C:
        return {
            'case': '1200C_1630C_kress91_authoritative',
            'status': 'authoritative',
            'source': 'REF-001 Kress91 1200-1630 C calibration band',
            'authoritative': True,
            'extrapolation': False,
            'high_uncertainty': False,
        }
    # CASE 1630-2100 C: Kress91 extrapolation experimentally confirmed by
    # Aithala, Macris & Hirschmann 2026 (REF-053, doi:10.7185/geochemlet.2617);
    # retain Kress91 rather than switching models.
    if T_C <= KRESS91_AITHALA_EXPERIMENTAL_CONFIRMATION_MAX_T_C:
        return {
            'case': '1630C_2100C_extrapolation_experimentally_confirmed',
            'status': 'extrapolation_experimentally_confirmed',
            'source': (
                'REF-001 Kress91 retained; REF-053 confirms high-T extrapolation'
            ),
            'authoritative': False,
            'extrapolation': True,
            'high_uncertainty': False,
        }
    # CASE 2100-2500 C: extrapolation with growing uncertainty; REF-053 is
    # the nearest experimental confirmation, but uncertainty grows beyond it.
    if T_C <= KRESS91_HIGH_UNCERTAINTY_MAX_T_C:
        return {
            'case': '2100C_2500C_extrapolation_growing_uncertainty',
            'status': 'high_uncertainty_extrapolation',
            'source': 'REF-001 Kress91 retained beyond REF-053 confirmation band',
            'authoritative': False,
            'extrapolation': True,
            'high_uncertainty': True,
        }
    # CASE >2500 C: F0-style high-uncertainty flag. The caller may refuse a
    # transaction; diagnostics de-authorize the temperature band either way.
    return {
        'case': 'above_2500C_deauthorized_high_uncertainty',
        'status': 'deauthorized_high_uncertainty',
        'source': 'REF-001 Kress91 outside authorized temperature envelope',
        'authoritative': False,
        'extrapolation': True,
        'high_uncertainty': True,
    }


def kress91_ln_fO2_temperature_delta(
    reference_T_K: float,
    target_T_K: float,
    *,
    reference_pressure_bar: float | None = None,
    target_pressure_bar: float | None = None,
) -> float:
    """Return the Kress91 ln(fO2) shift for fixed redox composition."""

    controls = {
        'reference_T_K': reference_T_K,
        'target_T_K': target_T_K,
    }
    for name, value in controls.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise Kress91InvalidControls(
                f'Kress91 invalid control {name}: expected finite positive '
                f'value, got {value!r}'
            ) from exc
        if not math.isfinite(number) or number <= 0.0:
            raise Kress91InvalidControls(
                f'Kress91 invalid control {name}: expected finite positive '
                f'value, got {value!r}'
            )
    reference_term = _kress91_temperature_pressure_term(
        float(reference_T_K),
        reference_pressure_bar,
    )
    target_term = _kress91_temperature_pressure_term(
        float(target_T_K),
        target_pressure_bar,
    )
    # Fixed Fe3+/Fe2+ means a*Delta ln(fO2) exactly cancels the endpoint
    # difference of the Kress91 temperature+pressure family.  The omitted
    # -3.36*dG term was 0.049 dex per +100 C inside the calibrated band
    # and 0.083 dex per +100 C across reachable extrapolations.
    return -(target_term - reference_term) / KRESS91_LN_FO2_COEFFICIENT


def _kress91_temperature_pressure_term(
    T_K: float,
    pressure_bar: float | None = None,
) -> float:
    term = KRESS91_INV_T_COEFFICIENT_K / float(T_K)
    term += KRESS91_NONLINEAR_COEFFICIENT * (
        1.0
        - (KRESS91_NONLINEAR_REFERENCE_T_K / float(T_K))
        - math.log(float(T_K) / KRESS91_NONLINEAR_REFERENCE_T_K)
    )
    if pressure_bar is None:
        return term
    try:
        p_bar = float(pressure_bar)
    except (TypeError, ValueError) as exc:
        raise Kress91InvalidControls(
            'Kress91 invalid control pressure_bar: expected finite positive '
            f'value or None, got {pressure_bar!r}'
        ) from exc
    if not math.isfinite(p_bar) or p_bar <= 0.0:
        raise Kress91InvalidControls(
            'Kress91 invalid control pressure_bar: expected finite positive '
            f'value or None, got {pressure_bar!r}'
        )
    p_pa = max(p_bar, 1.0e-9) * 100000.0
    return (
        term
        + KRESS91_PRESSURE_INV_T_COEFFICIENT * (p_pa / float(T_K))
        + KRESS91_PRESSURE_D_T_COEFFICIENT
        * (((float(T_K) - KRESS91_NONLINEAR_REFERENCE_T_K) * p_pa) / float(T_K))
        + KRESS91_PRESSURE_SQUARED_COEFFICIENT * ((p_pa ** 2.0) / float(T_K))
    )


def kress91_referenced_log_fO2(
    fO2_log: float,
    *,
    reference_T_K: float | None,
    target_T_K: float,
    reference_pressure_bar: float | None = None,
    target_pressure_bar: float | None = None,
) -> float:
    redox_fO2_log = float(fO2_log)
    redox_target_T_K = float(target_T_K)
    # 273.15 K is the exact Celsius-to-kelvin offset, so this is the
    # documented 1200 C liquid-calibration floor expressed in kelvin.
    calibration_min_T_K = KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15
    if redox_target_T_K < calibration_min_T_K:
        raise Kress91InvalidControls(
            f'Kress91 invalid control target_T_K: {redox_target_T_K!r} K is '
            'below liquid calibration floor '
            f'{calibration_min_T_K!r} K'
        )
    if reference_T_K is None:
        return redox_fO2_log
    redox_reference_T_K = float(reference_T_K)
    if redox_reference_T_K < calibration_min_T_K:
        # A reference in the REF-001 mid-band is corrupt persisted state and
        # must fail loud rather than silently bypassing the calibration band.
        raise Kress91InvalidControls(
            'Kress91 invalid control reference_T_K: '
            f'{redox_reference_T_K!r} K is below liquid calibration floor '
            f'{calibration_min_T_K!r} K'
        )
    delta_ln_fO2 = kress91_ln_fO2_temperature_delta(
        redox_reference_T_K,
        redox_target_T_K,
        reference_pressure_bar=reference_pressure_bar,
        target_pressure_bar=target_pressure_bar,
    )
    return (redox_fO2_log * math.log(10.0) + delta_ln_fO2) / math.log(10.0)


def _kress91_gate_key_referenced_log_fO2(
    fO2_log: float,
    *,
    reference_T_K: float | None,
) -> float:
    # This is the single reference boundary for local and PT0 gate keys.
    # Referenced fO2 is re-expressed at the Fe-redox reference temperature
    # 1673.15 K (KRESS91_FO2_KEY_REFERENCE_T_K, fe_redox.py:68) at fixed melt
    # redox state. Gate control T_K=298.15 K is a schema sentinel, not that T.
    # Production-function sanity check: raw -9.0 at 1450 C and -7.859211790826262
    # at 1600 C each reference to -9.438391...; a second conversion gives
    # -9.876783... and -11.017571..., key-quantized to -9.877 and -11.018.
    redox_fO2_log = float(fO2_log)
    if reference_T_K is None:
        return redox_fO2_log
    redox_reference_T_K = float(reference_T_K)
    if redox_reference_T_K <= 273.15:
        return redox_fO2_log
    return kress91_referenced_log_fO2(
        redox_fO2_log,
        reference_T_K=redox_reference_T_K,
        target_T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
    )


# Holzheid, Palme & Chakraborty 1997, DOI 10.1016/S0009-2541(97)00030-2:
# gamma_FeO(wustite(l)) = 1.70 +/- 0.22; stoich-FeO(l) multipliers below.
HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL = 1.70
HOLZHEID_FEO_GAMMA_WUSTITE_SIGMA = 0.22
HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C = (
    (1300.0, 2.02),
    (1400.0, 1.94),
    (1600.0, 1.66),
)

# Holzheid et al. 1997, DOI 10.1016/S0009-2541(97)00030-2:
# Delta G("FeO"_l) = -244118 + 115.559*T - 8.474*T*ln(T) J/mol.
HOLZHEID_FEO_LIQUID_DG_A_J_MOL = -244118.0
HOLZHEID_FEO_LIQUID_DG_B_J_MOL_K = 115.559
HOLZHEID_FEO_LIQUID_DG_C_J_MOL_K = -8.474

# Ban-ya 1993, ISIJ Int. 33:2-11, DOI not present in local OCR:
# clean OCR alpha_ij values in J from docs-private/.../ocr-extracted-params.md.
BAN_YA_ALPHA_J: dict[frozenset[str], float] = {
    frozenset(('Fe2', 'Fe3')): -18660.0,
    frozenset(('Fe2', 'Mn')): 7110.0,
    frozenset(('Fe2', 'Ca')): -31380.0,
    frozenset(('Fe2', 'Mg')): 33470.0,
    frozenset(('Fe2', 'Si')): -41840.0,
    frozenset(('Fe2', 'P')): -31380.0,
    frozenset(('Fe2', 'Al')): -41000.0,
    frozenset(('Fe3', 'Mn')): -56480.0,
    frozenset(('Fe3', 'Ca')): -95810.0,
    frozenset(('Fe3', 'Mg')): -2930.0,
    frozenset(('Fe3', 'Si')): 32640.0,
    frozenset(('Fe3', 'P')): 14640.0,
    frozenset(('Fe3', 'Al')): -161080.0,
    frozenset(('Mn', 'Ca')): -92050.0,
    frozenset(('Mn', 'Mg')): 61920.0,
    frozenset(('Mn', 'Si')): -75310.0,
    frozenset(('Mn', 'P')): -84940.0,
    frozenset(('Mn', 'Al')): -83680.0,
    frozenset(('Ca', 'Mg')): -100420.0,
    frozenset(('Ca', 'Si')): -133890.0,
    frozenset(('Ca', 'P')): -251040.0,
    frozenset(('Ca', 'Al')): -154810.0,
    frozenset(('Mg', 'Si')): -66940.0,
    frozenset(('Mg', 'P')): -37660.0,
    frozenset(('Mg', 'Al')): -71130.0,
    frozenset(('Si', 'P')): 83680.0,
    frozenset(('Si', 'Al')): -127610.0,
    frozenset(('P', 'Al')): -261500.0,
    frozenset(('Ti', 'Ca')): -167360.0,
    frozenset(('Ti', 'Mn')): -66940.0,
    frozenset(('Ti', 'Fe2')): -37660.0,
    frozenset(('Ti', 'Fe3')): 1260.0,
    frozenset(('Ti', 'Si')): 104600.0,
}
_BAN_YA_ALPHA_J_BY_CATION: dict[str, dict[str, float]] = {}
for _pair, _value in BAN_YA_ALPHA_J.items():
    _cation_a, _cation_b = tuple(_pair)
    _BAN_YA_ALPHA_J_BY_CATION.setdefault(_cation_a, {})[_cation_b] = _value
    _BAN_YA_ALPHA_J_BY_CATION.setdefault(_cation_b, {})[_cation_a] = _value

FEO_ACTIVITY_DIAGNOSTIC_SOURCES = {
    'holzheid_gamma': (
        'Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 '
        'gamma_FeO_wustite=1.70+-0.22'
    ),
    'holzheid_stoich_conversion': (
        'Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 '
        'gamma_stoich/gamma_wustite=2.02@1300C,1.94@1400C,1.66@1600C'
    ),
    'holzheid_dg_feo_l': (
        'Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 '
        'DeltaG=-244118+115.559*T-8.474*T*ln(T) J/mol'
    ),
    'banya_quadratic_alpha': (
        'Ban-ya1993 ISIJ Int. 33:2-11 DOI:not_in_local_ocr '
        'clean alpha_ij values from local OCR'
    ),
    'oneill_eggins_subregular': (
        'ONeillEggins2002 Chem.Geol.186:151-181 DOI:not_in_local_artifacts '
        'ln_gamma=sum_jk a_jk Xj Xk form from StepA'
    ),
    'li_coexistence': (
        'Li2018 Metals 8:714 DOI 10.3390/met8090714 '
        'coexistence-theory N_FeO form tracked, not solved in StepB'
    ),
    'wood_wade_low_bound': (
        'WoodWade2013 DOI 10.1007/s00410-013-0911-8 '
        'low-side gamma_FeO near unity from StepA grounding'
    ),
}

# Redox v3 Step C authority switch: Kress & Carmichael 1991 ferric split above
# IW(pure-FeO)+1; Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 central
# stoichiometric-FeO(l) band at/below IW(pure-FeO); Ban-ya1993 ISIJ Int. 33:2-11 carries
# the regular-solution composition-transfer diagnostic.
CALPHAD_AUTHORITY_BLEND_WIDTH_LOG10 = 1.0


def _validate_kress91_controls(
    *,
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
) -> None:
    controls = {
        'fO2_log': (fO2_log, False),
        'T_K': (T_K, True),
        'pressure_bar': (pressure_bar, True),
    }
    for name, (value, positive) in controls.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise Kress91InvalidControls(
                f'Kress91 invalid control {name}: expected finite'
                f'{" positive" if positive else ""} value, got {value!r}'
            ) from exc
        if not math.isfinite(number) or (positive and number <= 0.0):
            raise Kress91InvalidControls(
                f'Kress91 invalid control {name}: expected finite'
                f'{" positive" if positive else ""} value, got {value!r}'
            )


def floor_vacuum_pressure_bar(
    pressure_bar: float,
    *,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    """Floor a FINITE non-positive (vacuum) pressure to the Kress91 numerical
    floor, but pass NON-finite pressure through unchanged so the Kress91
    chokepoint validator (_validate_kress91_controls) refuses it.

    `max(p, floor)` silently masks -inf (returns floor), hiding an invalid
    control.
    """
    p = float(pressure_bar)
    if math.isfinite(p) and p <= 0.0:
        floor = float(floor_bar)
        if not math.isfinite(floor) or floor <= 0.0:
            raise Kress91InvalidControls(
                'Kress91 invalid control pressure_floor_bar: expected finite'
                f' positive value, got {floor_bar!r}'
            )
        return floor
    return p


def kress91_furnace_activity_pressure_bar(
    *,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    """Fixed pressure control for furnace FeO activity in vapor equilibrium."""

    # Kress91 pressure terms are high-pressure redox-split corrections. Neutral
    # furnace overhead is transport only, so vapor-equilibrium activity must not
    # read p_total. Coefficient provenance is recorded at the module constants.
    return floor_vacuum_pressure_bar(0.0, floor_bar=floor_bar)


def _linear_interpolate_or_clamp(
    points: tuple[tuple[float, float], ...],
    x: float,
) -> tuple[float, str]:
    if x <= points[0][0]:
        return points[0][1], 'clamped_below_verified_range'
    if x >= points[-1][0]:
        return points[-1][1], 'clamped_above_verified_range'
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            frac = (x - x0) / (x1 - x0)
            return y0 + frac * (y1 - y0), 'interpolated_verified_range'
    return points[-1][1], 'clamped_above_verified_range'


def holzheid_stoich_feo_gamma_band(T_K: float) -> dict[str, object]:
    T_C = float(T_K) - 273.15
    multiplier, status = _linear_interpolate_or_clamp(
        HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C,
        T_C,
    )
    central = HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL * multiplier
    sigma = HOLZHEID_FEO_GAMMA_WUSTITE_SIGMA * multiplier
    return {
        'basis': 'stoichiometric_FeO_l',
        'temperature_C': T_C,
        'conversion_multiplier': multiplier,
        'conversion_status': status,
        'central': central,
        'measurement_low': max(0.0, central - sigma),
        'measurement_high': central + sigma,
        'measurement_sigma': sigma,
        'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['holzheid_gamma'],
        'conversion_source': (
            FEO_ACTIVITY_DIAGNOSTIC_SOURCES['holzheid_stoich_conversion']
        ),
    }


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
    """Holzheid Eq. 9 wustite-liquid reference (including formation buffers)."""
    activity = max(float(a_feo), 1.0e-300)
    R = 8.31446261815324
    ln_fO2 = 2.0 * (
        math.log(activity)
        + holzheid_feo_liquid_delta_g_j_mol(T_K) / (R * float(T_K))
    )
    return ln_fO2 / math.log(10.0)


CONVENTIONAL_IW_BUFFER = 'solid_iron_wustite_sato1973'
CONVENTIONAL_IW_SOURCE = 'Sato, Hickling & McLane 1973, p.1067 Eq.5 (850-1300 C)'


def conventional_iw_log10_fO2_bar(T_K: float) -> float:
    """Conventional solid Fe/wustite IW; extrapolation is reported by the seed."""
    if not math.isfinite(T_K) or T_K <= 0:
        raise Kress91InvalidControls('conventional IW requires finite positive T_K')
    # Sato et al. 1973 p.1067 Eq.5: log10(fO2/atm)=6.824-27470/T[K].
    # 1 atm=1.01325 bar exactly; converting the dimensionless fugacity
    # ratio adds log10(1.01325). The inverse-T coefficient has units K.
    # At 1673.15 K this is -9.5884168484 bar-dex; delta=0 recovers IW.
    return 6.824 - 27470.0 / T_K + math.log10(1.01325)


def _feo_activity_saturation_log10_fO2_bar(T_K: float, *, a_feo: float) -> float:
    """Fe saturation for the activity owner's stoichiometric-FeO standard."""
    multiplier = float(holzheid_stoich_feo_gamma_band(T_K)['conversion_multiplier'])
    # Premise: gamma_s=m*gamma_w; mu_w^0+RT ln(a_w)=mu_s^0+RT ln(a_s).
    # Thus dG_s=dG_w-RT ln(m), and ln(a_s)+dG_s/(RT) equals
    # ln(a_s/m)+dG_w/(RT). Evaluate through the unchanged wustite owner.
    # Both logarithm arguments and m are dimensionless; RT ln(m) is J/mol.
    # m=1 recovers the original reference. At 1673.15 K, m=1.94 removes
    # the erroneous +2 log10(1.94)=0.575603 dex, without tuning gamma.
    return feo_iw_log10_fO2_bar(T_K, a_feo=float(a_feo) / multiplier)


# This identifier follows the coded line's wustite-liquid standard. Holzheid
# et al. (1997), Eq. 9, constructed it from O'Neill and Pownceby (1993) and
# Barin (1989); the paper states no uncertainty for the line, and this code
# states no validity range.
FE_FEO_BUFFER_STANDARD = 'wustite_FeO_l_holzheid1997_eq9'
REDOX_BUFFER_LINES: Mapping[str, Callable[[float], float]] = MappingProxyType(
    {
        FE_FEO_BUFFER_STANDARD: lambda T_K: feo_iw_log10_fO2_bar(
            T_K,
            a_feo=1.0,
        ),
    }
)


class RedoxStateInputError(ValueError):
    """Invalid or inconsistent redox-state request inputs."""


def _redox_state_finite_float(value: object, name: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f'{name} must be numeric')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f'{name} must be numeric') from exc
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite')
    return number


@dataclass(frozen=True, slots=True)
class RedoxState:
    """Immutable absolute fO2 or a registered buffer line plus an offset."""

    kind: Literal['absolute', 'buffer_offset']
    log_fO2: float | None = None
    buffer: str | None = None
    delta_log10: float | None = None

    def __post_init__(self) -> None:
        if self.kind == 'absolute':
            if self.buffer is not None or self.delta_log10 is not None:
                raise ValueError('absolute redox state requires only log_fO2')
            object.__setattr__(
                self,
                'log_fO2',
                _redox_state_finite_float(self.log_fO2, 'log_fO2'),
            )
            return
        if self.kind == 'buffer_offset':
            if self.log_fO2 is not None or self.buffer is None:
                raise ValueError(
                    'buffer_offset redox state requires buffer and delta_log10 only'
                )
            if not isinstance(self.buffer, str):
                raise TypeError('buffer must be a registered string identifier')
            if self.buffer not in REDOX_BUFFER_LINES:
                raise ValueError(f'unregistered redox buffer line: {self.buffer!r}')
            object.__setattr__(
                self,
                'delta_log10',
                _redox_state_finite_float(self.delta_log10, 'delta_log10'),
            )
            return
        raise ValueError(f'unsupported redox-state kind: {self.kind!r}')

    @classmethod
    def absolute(cls, log_fO2: float) -> 'RedoxState':
        return cls(kind='absolute', log_fO2=log_fO2)

    @classmethod
    def buffer_offset(cls, buffer: str, delta: float) -> 'RedoxState':
        return cls(kind='buffer_offset', buffer=buffer, delta_log10=delta)

    def log_fO2_at(self, T_K: float) -> float:
        temperature = _redox_state_finite_float(T_K, 'T_K')
        if temperature <= 0.0:
            raise ValueError('T_K must be positive')
        if self.kind == 'absolute':
            assert self.log_fO2 is not None
            return self.log_fO2
        assert self.buffer is not None
        assert self.delta_log10 is not None
        return REDOX_BUFFER_LINES[self.buffer](temperature) + self.delta_log10

    def to_descriptor(self) -> dict[str, str | float]:
        if self.kind == 'absolute':
            assert self.log_fO2 is not None
            return {'kind': 'absolute', 'log_fO2': self.log_fO2}
        assert self.buffer is not None
        assert self.delta_log10 is not None
        return {
            'kind': 'buffer_offset',
            'buffer': self.buffer,
            'delta_log10': self.delta_log10,
        }

    def cache_identity(self) -> dict[str, str | float] | None:
        """Return the non-absolute state identity carried beside scalar fO2."""
        if self.kind == 'absolute':
            return None
        return self.to_descriptor()


def _non_absolute_redox_state_kwargs(
    redox_state: RedoxState | None,
) -> dict[str, RedoxState]:
    """Forward only redox states that add cache identity beyond scalar fO2."""
    if redox_state is None or redox_state.cache_identity() is None:
        return {}
    return {'redox_state': redox_state}


def normalize_redox_state(
    fO2_log: float | None,
    redox_state: RedoxState | None = None,
) -> tuple[float | None, RedoxState | None]:
    """Normalize the scalar/state pair while preserving omitted controls."""
    if fO2_log is None and redox_state is None:
        return None, None

    scalar = (
        None
        if fO2_log is None
        else _redox_state_finite_float(fO2_log, 'fO2_log')
    )
    if redox_state is None:
        assert scalar is not None
        return scalar, RedoxState.absolute(scalar)

    reference_value = _redox_state_finite_float(
        redox_state.log_fO2_at(REDOX_STATE_REFERENCE_T_K),
        'redox_state reference fO2',
    )
    if scalar is None:
        return reference_value, redox_state
    if not math.isclose(
        scalar,
        reference_value,
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise RedoxStateInputError(
            'fO2_log disagrees with redox_state at '
            f'{REDOX_STATE_REFERENCE_T_K} K: {scalar!r} != {reference_value!r}'
        )
    return scalar, redox_state


def _reanchor_redox_state(
    redox_state: RedoxState | None,
    fO2_log: float | None,
) -> RedoxState | None:
    """Move a state to a canonical scalar at the owner reference temperature."""
    if fO2_log is None:
        return redox_state
    canonical = _redox_state_finite_float(fO2_log, 'fO2_log')
    if redox_state is None or redox_state.kind == 'absolute':
        return RedoxState.absolute(canonical)
    current = _redox_state_finite_float(
        redox_state.log_fO2_at(REDOX_STATE_REFERENCE_T_K),
        'redox_state reference fO2',
    )
    assert redox_state.buffer is not None
    assert redox_state.delta_log10 is not None
    return RedoxState.buffer_offset(
        redox_state.buffer,
        redox_state.delta_log10 + (canonical - current),
    )


def _alpha_j(cation_a: str, cation_b: str) -> float | None:
    if cation_a == cation_b:
        return 0.0
    row = _BAN_YA_ALPHA_J_BY_CATION.get(cation_a)
    return None if row is None else row.get(cation_b)


def _melt_cation_fractions(
    comp_wt: Mapping[str, float],
    *,
    fe3_over_sigma_fe: float,
) -> dict[str, float]:
    from simulator.state import MOLAR_MASS

    cation_mol: dict[str, float] = {}

    def add(oxide: str, cation: str, count: float) -> None:
        wt = max(0.0, float(comp_wt.get(oxide, 0.0) or 0.0))
        mm = float(MOLAR_MASS.get(oxide, 0.0) or 0.0)
        if wt > 0.0 and mm > 0.0:
            cation_mol[cation] = cation_mol.get(cation, 0.0) + wt / mm * count

    fe_total = feot_equivalent_wt_pct(comp_wt) / 71.844
    if fe_total > 0.0:
        fe3 = max(0.0, min(1.0, float(fe3_over_sigma_fe)))
        cation_mol['Fe2'] = fe_total * (1.0 - fe3)
        cation_mol['Fe3'] = fe_total * fe3

    add('MnO', 'Mn', 1.0)
    add('CaO', 'Ca', 1.0)
    add('MgO', 'Mg', 1.0)
    add('SiO2', 'Si', 1.0)
    add('P2O5', 'P', 2.0)
    add('Al2O3', 'Al', 2.0)
    add('TiO2', 'Ti', 1.0)

    total = sum(max(0.0, value) for value in cation_mol.values())
    if total <= 0.0:
        return {}
    return {
        cation: mol / total
        for cation, mol in cation_mol.items()
        if mol > 0.0
    }


def ban_ya_quadratic_gamma_feo(
    cation_fractions: Mapping[str, float],
    *,
    T_K: float,
) -> dict[str, object]:
    candidate = {
        cation: max(0.0, float(value))
        for cation, value in cation_fractions.items()
        if cation != 'Fe2' and float(value) > 0.0
    }
    missing: list[str] = []
    active = [
        cation for cation in candidate
        if _alpha_j('Fe2', cation) is not None
    ]
    for cation in sorted(set(candidate) - set(active)):
        missing.append(f'Fe2-{cation}')

    changed = True
    while changed:
        changed = False
        for index, cation_a in enumerate(tuple(active)):
            for cation_b in tuple(active)[index + 1:]:
                if _alpha_j(cation_a, cation_b) is None:
                    drop = min(
                        (cation_a, cation_b),
                        key=lambda c: candidate.get(c, 0.0),
                    )
                    active.remove(drop)
                    missing.append(f'{cation_a}-{cation_b}')
                    changed = True
                    break
            if changed:
                break

    if not active:
        return {
            'status': 'unavailable',
            'gamma': 1.0,
            'rt_ln_gamma_J_mol': 0.0,
            'active_cations': [],
            'excluded_or_missing_pairs': missing,
            'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['banya_quadratic_alpha'],
        }

    rt_ln_gamma = 0.0
    for cation in active:
        rt_ln_gamma += (
            _alpha_j('Fe2', cation) or 0.0
        ) * candidate[cation] ** 2
    for index, cation_a in enumerate(active):
        for cation_b in active[index + 1:]:
            alpha_fe_a = _alpha_j('Fe2', cation_a) or 0.0
            alpha_fe_b = _alpha_j('Fe2', cation_b) or 0.0
            alpha_ab = _alpha_j(cation_a, cation_b)
            if alpha_ab is None:
                continue
            rt_ln_gamma += (
                alpha_fe_a + alpha_fe_b - alpha_ab
            ) * candidate[cation_a] * candidate[cation_b]

    R = 8.31446261815324
    ln_gamma = rt_ln_gamma / (R * float(T_K))
    gamma = math.exp(max(-745.0, min(709.0, ln_gamma)))
    return {
        'status': 'ok' if not missing else 'ok_with_ocr_gaps',
        'gamma': gamma,
        'ln_gamma': ln_gamma,
        'rt_ln_gamma_J_mol': rt_ln_gamma,
        'active_cations': active,
        'excluded_or_missing_pairs': missing,
        'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['banya_quadratic_alpha'],
    }


def _oneill_eggins_subregular_shape(
    cation_fractions: Mapping[str, float],
) -> dict[str, object]:
    return {
        'status': 'not_digitized_stepB',
        'reason': (
            'subregular form retained from StepA, but exact coefficient table '
            'not carried into StepB runtime without OCR line-level provenance'
        ),
        'active_cations_seen': sorted(
            cation for cation in cation_fractions if cation in {'Ca', 'Mg', 'Al', 'Si'}
        ),
        'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['oneill_eggins_subregular'],
    }


def melt_mol_fractions_for_kress91(comp_wt: Mapping[str, float]) -> dict[str, float]:
    # Lazy import: this module is imported by engines/builtin providers (R2.1b),
    # whose import guard (engines/builtin/__init__.py) forbids provider top-level
    # simulator.state imports. vapor_pressure.py uses the same lazy pattern for
    # GAS_CONSTANT. Keeping fe_redox.py a true leaf avoids that cycle.
    from simulator.state import MOLAR_MASS

    try:
        feo_wt, fe2o3_wt = iron_oxide_values(comp_wt)
    except ValueError as exc:
        raise Kress91InvalidControls(
            'Kress91 composition FeO/Fe2O3 must be finite and non-negative'
        ) from exc
    iron_oxides = {'FeO': feo_wt, 'Fe2O3': fe2o3_wt}
    for oxide in KRESS91_MOL_FRACTION_OXIDES:
        raw = comp_wt.get(oxide, 0.0)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise Kress91InvalidControls(
                f'Kress91 composition {oxide} must be finite and non-negative'
            ) from exc
        if not math.isfinite(value) or value < 0.0:
            raise Kress91InvalidControls(
                f'Kress91 composition {oxide} must be finite and non-negative'
            )
    for oxide, value in iron_oxides.items():
        if not math.isfinite(value) or value < 0.0:
            raise Kress91InvalidControls(
                f'Kress91 composition {oxide} must be finite and non-negative'
            )
    feot_wt = feot_equivalent_wt_pct(comp_wt)
    mol_counts: dict[str, float] = {}
    for oxide in KRESS91_MOL_FRACTION_OXIDES:
        wt = float(comp_wt.get(oxide, 0.0) or 0.0)
        molar_mass = float(MOLAR_MASS.get(oxide, 0.0) or 0.0)
        if wt > 0.0 and molar_mass > 0.0:
            mol_counts[oxide] = wt / molar_mass
        else:
            mol_counts[oxide] = 0.0
    mol_counts['FeOt'] = feot_wt / 71.844 if feot_wt > 0.0 else 0.0
    total_mol = sum(mol_counts.values())
    if total_mol <= 0.0:
        return {}
    return {oxide: mol / total_mol for oxide, mol in mol_counts.items()}


def kress91_dropped_components(
    comp_wt: Mapping[str, float],
) -> tuple[tuple[str, ...], float | None]:
    """Report positive composition mass excluded by the KC91 oxide basis."""

    supported = {*KRESS91_MOL_FRACTION_OXIDES, 'FeO', 'Fe2O3'}
    values = {name: float(amount) for name, amount in comp_wt.items()}
    dropped = tuple(
        sorted(name for name, amount in values.items() if name not in supported and amount > 0.0)
    )
    total = math.fsum(amount for amount in values.values() if amount > 0.0)
    if not dropped or total <= 0.0:
        return (), None
    dropped_total = math.fsum(values[name] for name in dropped)
    return dropped, dropped_total / total


def _kress91_fe2o3_over_feo_molar(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    _validate_kress91_controls(
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    x = mol_fractions
    p_pa = max(float(pressure_bar), 1.0e-9) * 100000.0
    ln_ratio = (
        # a*ln(fO2) with fO2 = 10**fO2_log, computed as fO2_log*ln(10) directly.
        # The prior 10.0**fO2_log underflows to 0.0 at extreme-reducing fO2 and
        # then math.log(0.0) raises a domain error, aborting the provider (BUG-159).
        # This form is algebraically exact and is the canonical Kress91 a*ln(fO2)
        # term (the sibling exp() at the return is already domain-clamped).
        KRESS91_LN_FO2_COEFFICIENT * float(fO2_log) * math.log(10.0)
        + KRESS91_INV_T_COEFFICIENT_K / float(T_K)
        - 6.675
        - 2.243 * x.get('Al2O3', 0.0)
        - 1.828 * x.get('FeOt', 0.0)
        + 3.201 * x.get('CaO', 0.0)
        + 5.854 * x.get('Na2O', 0.0)
        + 6.215 * x.get('K2O', 0.0)
        + KRESS91_NONLINEAR_COEFFICIENT * (
            1.0
            - (KRESS91_NONLINEAR_REFERENCE_T_K / T_K)
            - math.log(T_K / KRESS91_NONLINEAR_REFERENCE_T_K)
        )
        + KRESS91_PRESSURE_INV_T_COEFFICIENT * (p_pa / T_K)
        + KRESS91_PRESSURE_D_T_COEFFICIENT * (
            ((T_K - KRESS91_NONLINEAR_REFERENCE_T_K) * p_pa) / T_K
        )
        + KRESS91_PRESSURE_SQUARED_COEFFICIENT * ((p_pa ** 2.0) / T_K)
    )
    return math.exp(max(-745.0, min(709.0, ln_ratio)))


def kress91_fe3_over_sigma_fe(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    ratio = _kress91_fe2o3_over_feo_molar(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    return 2.0 * ratio / (2.0 * ratio + 1.0)


def _native_fe_saturation_band(q: float) -> tuple[float, float]:
    # Jayasuriya et al. (2004), Am Mineral 89, 1597, abstract:
    # doi:10.2138/am-2004-11-1203, Mossbauer q=Fe3+/SumFe precision
    # +/-0.01 (1 sigma). Held-out near-IW Jayasuriya forward residuals
    # (b917-r2, "Jayasuriya forward comparison") are +.0046, +.0235, +.0122
    # in q. Their RMS is a model-error magnitude, NOT a fitted bias correction.
    # R=q/(1-q); log10 R=a*log10 fO2+const, a=0.196 (Kress91).
    # Thus each finite interval endpoint shifts log fO2 by log(R_end/R)/a.
    # d(log10 R)/dq = 1/[ln(10)*q*(1-q)], hence model sigma in dex is
    # RMS(residuals)/[a*ln(10)*q*(1-q)]. Combine it in quadrature with EACH
    # finite method-interval endpoint. All inputs are dimensionless, output
    # is dex. Zero residuals recover the method-only band; no bias is removed.
    # q-.01<=0 leaves the reducing end unbounded, rather than clipping it.
    if q <= 0.0 or q >= 1.0:
        return math.inf, math.inf
    log_r = math.log10(q / (1.0 - q))
    low, high = q - 0.01, q + 0.01
    lower = (math.inf if low <= 0.0 else
             (log_r - math.log10(low / (1.0 - low))) / KRESS91_LN_FO2_COEFFICIENT)
    upper = (math.inf if high >= 1.0 else
             (math.log10(high / (1.0 - high)) - log_r) / KRESS91_LN_FO2_COEFFICIENT)
    residual_rms = math.sqrt(math.fsum(r*r for r in (.0046, .0235, .0122)) / 3.0)
    model_sigma = residual_rms / (KRESS91_LN_FO2_COEFFICIENT * math.log(10.0) * q * (1.0-q))
    return math.hypot(lower, model_sigma), math.hypot(upper, model_sigma)


@dataclass(frozen=True)
class NativeFeSaturationNotice:
    flag: str
    melt_potential: float
    iw_bound: float
    a_feo: float
    saturation_gap_dex: float
    band_lower_dex: float | None
    band_upper_dex: float | None
    band_lower_unbounded: bool
    band_upper_unbounded: bool
    status: Literal['BELOW', 'ABOVE', 'INDETERMINATE', 'UNMEASURED_PRIOR_BELOW']


@dataclass(frozen=True)
class Kress91OutOfBandNotice:
    temperature_C: float
    min_temperature_C: float = KRESS91_LIQUID_CALIBRATION_MIN_T_C
    max_temperature_C: float = KRESS91_LIQUID_CALIBRATION_MAX_T_C
    authority: str = 'extrapolated'
    flag: str = 'KRESS91_OUT_OF_CALIBRATION_BAND'
    source: str = 'Kress & Carmichael 1991, doi:10.1007/BF00307328'


def _kress91_temperature_notices(temperature_C: float) -> list[dict[str, object]]:
    if kress91_temperature_band_case(temperature_C)['extrapolation']:
        return [asdict(Kress91OutOfBandNotice(temperature_C=temperature_C))]
    return []


def _native_fe_saturation_comparison(
    comp: Mapping[str, float],
    *,
    fe3_over_sigma_fe: float,
    T_K: float,
    pressure_bar: float,
    fO2_log: float,
    prior_half_width_dex: float = 0.0,
    seed_basis: str | None = None,
) -> dict[str, object]:
    from simulator.accounting.exceptions import AccountingError

    fe3 = float(fe3_over_sigma_fe)
    if not math.isfinite(fe3) or not 0.0 <= fe3 <= 1.0:
        raise ValueError(
            'fe3_over_sigma_fe must be finite and within [0, 1]'
        )
    ferrous_available = max(0.0, 1.0 - fe3)
    if ferrous_available <= 0.0 or feot_equivalent_wt_pct(comp) <= 0.0:
        return {
            'native_fe_saturation': False,
            'native_fe_threshold': 'FeO_activity_saturation',
            'native_fe_frac': 0.0,
        }
    try:
        activity = calphad_ferrous_feo_activity_diagnostic(
            comp_wt=comp,
            fO2_log=fO2_log,
            T_K=T_K,
            pressure_bar=pressure_bar,
            fe3_over_sigma_fe=fe3,
        )
        # The ONE native-saturation basis selection inherits t-1293's open
        # X versus gamma X question. Preserve the existing activity owner.
        a_feo = float(activity.get('a_FeO_authoritative', 0.0) or 0.0)
        pure_feo_iw = _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=1.0)
    except Exception as exc:
        raise AccountingError(
            'native Fe saturation split requires grounded FeO activity; '
            'CALPHAD/Holzheid FeO activity diagnostic failed'
        ) from exc
    if a_feo <= 0.0 or not math.isfinite(a_feo):
        return {
            'native_fe_saturation': False,
            'native_fe_threshold': 'FeO_activity_saturation',
            'native_fe_frac': 0.0,
            'native_fe_activity_source': 'unavailable:no_positive_a_FeO',
        }
    # FeO(l) = Fe(metal) + 1/2 O2; Holzheid et al. 1997
    # doi:10.1016/S0009-2541(97)00030-2 grounds FeO(l) DeltaG in
    # feo_iw_log10_fO2_bar(), so a_FeO,sat = 10^((log fO2 - IW)/2).
    saturation_log10 = (float(fO2_log) - pure_feo_iw) / 2.0
    if saturation_log10 > 300.0:
        a_feo_saturation = float('inf')
    elif saturation_log10 < -300.0:
        a_feo_saturation = 0.0
    else:
        a_feo_saturation = 10.0 ** saturation_log10
    bound = _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=a_feo)
    gap = float(fO2_log) - bound
    lower, upper = _native_fe_saturation_band(fe3)
    # A class prior is an epistemic interval, not an independent Gaussian.
    # With r=max(delta-lo, hi-delta), any prior error lies in [-r,+r].
    # Minkowski addition to the method+model interval [-L,+U] gives
    # [-L-r,+U+r], conservative at a FIXED saturation bound only, retaining
    # asymmetry without an independence assumption. This is not a nonlinear
    # enclosure of the full gap: changing the seed also changes q and a_FeO.
    # All terms are dex; r=0 recovers the existing band, infinity stays open.
    lower += prior_half_width_dex
    upper += prior_half_width_dex
    status = ('BELOW' if gap < -lower else
              'ABOVE' if gap > upper else 'INDETERMINATE')
    if seed_basis == 'class_prior' and status == 'BELOW':
        status = 'UNMEASURED_PRIOR_BELOW'
    band = {
        'band_lower_dex': lower if math.isfinite(lower) else None,
        'band_upper_dex': upper if math.isfinite(upper) else None,
        'band_lower_unbounded': math.isinf(lower),
        'band_upper_unbounded': math.isinf(upper),
    }
    notice = (None if status == 'ABOVE' else asdict(NativeFeSaturationNotice(
        flag=('REDOX_BELOW_FE_FEO_SATURATION' if status == 'BELOW' else
              'REDOX_UNMEASURED_PRIOR_BELOW' if status == 'UNMEASURED_PRIOR_BELOW' else
              'REDOX_FE_FEO_SATURATION_INDETERMINATE'),
        melt_potential=float(fO2_log), iw_bound=bound, a_feo=a_feo,
        saturation_gap_dex=gap, **band,
        status=status,
    )))
    if status == 'UNMEASURED_PRIOR_BELOW':
        notice['reason'] = 'Unmeasured class priors cannot authorize metallic Fe precipitation.'
    return {
        'native_fe_saturation': status == 'BELOW',
        'saturation_status': status,
        'saturation_notice': notice,
        **band,
        'native_fe_threshold': 'FeO_activity_saturation',
        'iw_bound_log': bound,
        'saturation_gap_dex': gap,
        'native_fe_activity': a_feo,
        'native_fe_saturation_activity': a_feo_saturation,
        'native_fe_pure_feo_iw_log': pure_feo_iw,
        'native_fe_activity_source': (
            'Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 '
            'FeO(l)=Fe+0.5O2 saturation'
        ),
    }


NATIVE_FE_ROOT_TOLERANCE_DEX = 1.0e-10


@dataclass(frozen=True)
class NativeFePrecipitationNotice:
    n_fe_mol: float
    fO2_before_log: float
    fO2_after_log: float
    iw_bound_after_log: float
    limit: Literal['melt', 'gas', 'both']
    # Jayasuriya 2004 reports reduced glass without observed metal. This
    # equilibrium extent does not establish a nucleation rate or barrier.
    authority: Literal['equilibrium_assumed'] = 'equilibrium_assumed'
    flag: str = 'NATIVE_FE_PRECIPITATION'


@dataclass(frozen=True)
class NativeFeGasLimitedNotice:
    melt_root_mol: float
    gas_root_mol: float
    gas_boundary_log: float
    iw_bound_log: float
    flag: str = 'NATIVE_FE_GAS_LIMITED'


@dataclass(frozen=True)
class NativeFeRemovalRateUnmodelledNotice:
    melt_root_mol: float
    authority: Literal['equilibrium_assumed'] = 'equilibrium_assumed'
    flag: Literal['removal_rate_unmodelled'] = 'removal_rate_unmodelled'


def _native_fe_open_sink_removal_cap(
    extent_mol: float, *, melt_root_mol: float,
) -> tuple[float, dict[str, object]]:
    # Pending b-901's actual stream owner: at total P, the carrier/O2 ratio
    # gives F_O2/F_total = p_IW/(P-p_IW). FeO -> Fe + 1/2 O2 therefore
    # requires n_Fe <= 2*F_total*p_IW/(P-p_IW)*dt. Here F_total is the
    # O2-free carrier stream in mol/s; pressure ratio is dimensionless,
    # so mol/s*s gives mol Fe. At p_IW -> 0 capacity tends to zero;
    # this expression requires 0 <= p_IW < P. No actual F_total exists on
    # this branch. Controller authorizes an equilibrium sink, explicitly
    # unmodelled in rate, rather than substituting a conductance capacity.
    return extent_mol, asdict(NativeFeRemovalRateUnmodelledNotice(melt_root_mol))


def native_fe_equilibrium_extent(
    *, comp_wt: Mapping[str, float], feo_mol: float, fe2o3_mol: float,
    T_K: float, pressure_bar: float,
    gas_fO2_log: Callable[[float], float],
    open_sink: bool = False,
    prior_half_width_dex: float = 0.0,
    seed_basis: str | None = None,
) -> dict[str, object]:
    """Instantaneous FeO -> Fe + 0.5 O2 extent at the supplied gas boundary."""
    from scipy.optimize import brentq

    if feo_mol <= 0.0:
        return {'native_fe_mol': 0.0, 'native_fe_frac': 0.0,
                'native_fe_state': {'native_fe_saturation': False,
                                    'native_fe_frac': 0.0},
                'removal_rate_notice': (
                    _native_fe_open_sink_removal_cap(0.0, melt_root_mol=0.0)[1]
                    if open_sink else None),
                }
    total_fe = feo_mol + 2.0 * fe2o3_mol

    def state(n):
        # Premise: ferric inventory stays fixed while n mol FeO leaves.
        # Debit w_FeO*n/F on the original weight basis, then renormalize.
        # n/F is dimensionless; at zero extent the composition is unchanged,
        # and n -> F removes all FeO. No kg inventory is introduced.
        comp = dict(comp_wt)
        comp.update(FeO=iron_oxide_values(comp)[0] * ((feo_mol - n) / feo_mol))
        total = sum(comp.values())
        comp = {s: 100.0*w/total for s, w in comp.items()}
        potential = float(kress91_intrinsic_state(
            feo_mol=feo_mol-n, fe2o3_mol=fe2o3_mol,
            mol_fractions=melt_mol_fractions_for_kress91(comp),
            T_K=T_K, pressure_bar=pressure_bar,
            exchange_fO2_log=gas_fO2_log(n), composition_wt_pct=comp,
        )['fO2_log'])
        comparison = _native_fe_saturation_comparison(
            comp, fe3_over_sigma_fe=2.0*fe2o3_mol/(total_fe-n), T_K=T_K,
            pressure_bar=pressure_bar, fO2_log=potential,
            prior_half_width_dex=prior_half_width_dex,
            seed_basis=seed_basis,
        )
        return potential, comparison

    before, initial = state(0.0)
    # F'=F-n, S'=N+F'+2C on the Kress FeOt molar basis. For the fitted
    # composition term B, dB/dn=D/S'^2, D=sum_i(c_i+1.828)*N_i.
    # On this reducing limb a_FeO=gamma(T)*F'/S', hence
    # g'=[(1+2*a_K)/F' - D/S'^2 - 2*a_K/S']/(a_K*ln(10)), in dex/mol.
    # Check, rather than assume, y=F'/S' <= 0.2 on this bracket.
    # Since c_i+1.828 <= 8.043, g' is bounded below by
    # [1.392/y - 0.392 - 8.043*(1-y)]/(0.196*ln(10)*S'). The numerator
    # decreases up to y=0.2, where it is 0.1336 > 0: g is strictly increasing.
    # At the root f_inv=IW(a)<=IW(1), so this bracket stays on the gamma X
    # limb. Beyond it the owner's gamma-to-X blend lowers activity further.
    # Also g -> +infinity as F' -> 0. Activity decreases, so
    # h=log(pO2_gas(n))-IW(a(n)) also increases for a nondecreasing gas
    # boundary. Reduction requires BOTH g<0 and h<0, giving the minimum
    # of their roots. Equal initial potentials give zero extent.
    y0 = (melt_mol_fractions_for_kress91(comp_wt)['FeOt']
          * feo_mol / total_fe)
    a_k = KRESS91_LN_FO2_COEFFICIENT
    derivative_lower_numerator = (
        (1.0 + 2.0*a_k)/y0 - 2.0*a_k - 8.043*(1.0-y0)
    )
    # 8.043 = max(c_i)-c_FeOt = 6.215-(-1.828), from Kress91 above.
    # The lower numerator decreases up to y=.2, while y decreases with n.
    # gamma*y0<1 excludes the activity cap. This proves g'>0 up to its
    # first crossing, which is below pure IW; the later blend can only
    # lower a_FeO and cannot introduce an earlier crossing.
    monotonic = bool(
        y0 <= 0.2 and derivative_lower_numerator > 0.0
        and float(holzheid_stoich_feo_gamma_band(T_K)['central'])*y0 < 1.0
    )
    def melt_gap(n):
        potential, comparison = state(n)
        return potential - float(comparison['iw_bound_log'])

    def gas_gap(n):
        _, comparison = state(n)
        return gas_fO2_log(n) - float(comparison['iw_bound_log'])

    hi = math.nextafter(feo_mol, 0.0)

    def root(gap):
        if gap(0.0) >= 0.0:
            return 0.0
        low, high = 0.0, hi
        if not monotonic:
            # No sign proof (e.g. prepared CI xFeOt=.270): search in
            # ascending extent for the first sampled sign change BEFORE
            # deciding from the far endpoint or handing a bracket to Brent.
            for i in range(1, 257):
                high = hi * (i / 256.0)
                if gap(high) >= 0.0:
                    break
                low = high
            else:
                return feo_mol
        elif gap(high) <= 0.0:
            return feo_mol
        n = brentq(gap, low, high, xtol=1e-300, rtol=1e-14, maxiter=200)
        if abs(gap(n)) > NATIVE_FE_ROOT_TOLERANCE_DEX:
            raise Kress91InvalidControls('native Fe root failed its dex tolerance')
        return n

    if not initial['native_fe_saturation']:
        melt_root = gas_root = n = 0.0
        limit = 'melt'
    else:
        melt_root = root(melt_gap)
        gas_root = root(gas_gap)
        n = min(melt_root, gas_root)
        limit = ('both' if melt_root == gas_root else
                 'melt' if melt_root < gas_root else 'gas')
    removal_notice = None
    if open_sink:
        n, removal_notice = _native_fe_open_sink_removal_cap(
            n, melt_root_mol=melt_root,
        )
    after, final = state(n)
    fraction = n / total_fe
    gas_notice = (asdict(NativeFeGasLimitedNotice(
        melt_root_mol=melt_root, gas_root_mol=gas_root,
        gas_boundary_log=gas_fO2_log(n), iw_bound_log=float(final['iw_bound_log']),
    )) if gas_root < melt_root else None)
    return {
        'native_fe_mol': n, 'native_fe_frac': fraction,
        'native_fe_state': {**initial, 'native_fe_frac': fraction},
        'gas_limited_notice': gas_notice,
        'removal_rate_notice': removal_notice,
        'fO2_before_log': before, 'fO2_after_log': after,
        'iw_bound_after_log': final['iw_bound_log'], 'limit': limit,
        'melt_root_mol': melt_root, 'gas_root_mol': gas_root,
        'melt_monotonicity_proven': monotonic,
    }


def _all_ferrous_feo_activity(
    *,
    comp_wt: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=comp_wt,
        T_K=T_K,
        pressure_bar=pressure_bar,
        fO2_log=feo_iw_log10_fO2_bar(T_K),
        fe3_over_sigma_fe=0.0,
    )
    a_feo = float(activity['a_FeO_authoritative'])
    if not math.isfinite(a_feo) or a_feo <= 0.0:
        raise Kress91InvalidControls(
            'all-ferrous state has no established FeO activity bound'
        )
    return a_feo


def kress91_intrinsic_state(
    *,
    feo_mol: float,
    fe2o3_mol: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
    exchange_fO2_log: float,
    redox_state: RedoxState | None = None,
    composition_wt_pct: Mapping[str, float] | None = None,
    prior_half_width_dex: float = 0.0,
    seed_basis: str | None = None,
) -> dict[str, object]:
    """Derive the liquid oxygen potential from its actual iron inventory."""
    if redox_state is not None:
        return {
            'fO2_log': redox_state.log_fO2_at(T_K),
            'exchange_bounded': False, 'flags': [],
        }
    _validate_kress91_controls(
        fO2_log=exchange_fO2_log, T_K=T_K, pressure_bar=pressure_bar,
    )
    for name, mol in (('FeO', feo_mol), ('Fe2O3', fe2o3_mol)):
        if not math.isfinite(mol) or mol < 0.0:
            raise Kress91InvalidControls(f'{name} mol must be finite non-negative')
    flags = []
    notices = _kress91_temperature_notices(T_K - 273.15)
    if notices:
        flags.append('OUT_OF_CERTIFIED_BAND')
    bounded = False
    bound = None
    if feo_mol > 0.0 and fe2o3_mol == 0.0:
        if composition_wt_pct is None:
            raise Kress91InvalidControls('all-ferrous state requires grounded FeO activity')
        a_feo = _all_ferrous_feo_activity(
            comp_wt=composition_wt_pct,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )
        # FeO(l)=Fe+1/2 O2: the existing activity owner sets saturation.
        # Below this lower bound metallic Fe can form; zero ferric inventory
        # cannot specify a finite Kress potential. This is a stability bound,
        # not an equilibrium assignment to the vacuum or the headspace gas.
        value = _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=a_feo)
        bound = 'lower'
        flags.append('FE_FEO_SATURATION_BOUND')
    elif feo_mol == 0.0 or fe2o3_mol == 0.0:
        # Kress has no finite inverse at either end (or with no iron couple).
        # The gas-side boundary supplies a finite potential, never a sentinel.
        value = min(exchange_fO2_log, math.log10(pressure_bar))
        bounded = True
        flags.append('EXCHANGE_BOUNDED')
        if exchange_fO2_log > value:
            flags.append('REDOX_PRESSURE_CEILING')
    else:
        # Premise (Kress & Carmichael 1991): ln(n_Fe2O3/n_FeO) = a ln fO2 + B,
        # B containing composition and the full T/P terms. Evaluate B through
        # the forward owner at fO2=1 bar (ln fO2=0), avoiding a second formula.
        # Algebra: log10(fO2/bar) = [ln(n_Fe2O3)-ln(n_FeO)-B]/[a ln(10)].
        # All logs are dimensionless; the mol units cancel in the ratio.
        # Limits: n_Fe2O3 -> 0 gives -infinity, n_FeO -> 0 gives +infinity;
        # those exact ends use exchange above. Sanity: at 1873.15 K, 0.01 bar,
        # the basalt test basis ratio 0.02275705282703544 returns -7.5 dex.
        baseline = _kress91_fe2o3_over_feo_molar(
            fO2_log=0.0, mol_fractions=mol_fractions,
            T_K=T_K, pressure_bar=pressure_bar,
        )
        value = (math.log(fe2o3_mol) - math.log(feo_mol) - math.log(baseline)) / (
            KRESS91_LN_FO2_COEFFICIENT * math.log(10.0)
        )
    state = {'fO2_log': value, 'exchange_bounded': bounded, 'bound': bound,
             'flags': flags, 'notices': notices}
    if bounded:
        state.update(unclamped_fO2_log=exchange_fO2_log,
                     pressure_ceiling_fO2_log=math.log10(pressure_bar))
    elif feo_mol > 0.0 and fe2o3_mol >= 0.0:
        if composition_wt_pct is not None:
            comparison = _native_fe_saturation_comparison(
                composition_wt_pct,
                fe3_over_sigma_fe=2.0 * fe2o3_mol / (feo_mol + 2.0 * fe2o3_mol),
                T_K=T_K, pressure_bar=pressure_bar, fO2_log=value,
                prior_half_width_dex=prior_half_width_dex,
                seed_basis=seed_basis,
            )
            # Premise: Kress91's ferric/ferrous ratio is a fit, not an
            # Fe/FeO equilibrium, so a finite low-ferric inverse can undercut
            # the separate Fe/FeO stability bound.
            # Algebra: FeO(l) = Fe + 1/2 O2 gives IW(a_FeO) =
            # IW + 2 log10(a_FeO).
            # Units: fO2 is relative to 1 bar and a_FeO is dimensionless, so
            # the logarithm arguments and result in dex are dimensionless.
            # Sanity: a_FeO=1 recovers IW; a_FeO<1 lowers the bound.
            state.update(
                a_feo=comparison['native_fe_activity'],
                iw_bound=comparison['iw_bound_log'],
                saturation_notice=comparison['saturation_notice'],
                saturation_status=comparison['saturation_status'],
                band_lower_dex=comparison['band_lower_dex'],
                band_upper_dex=comparison['band_upper_dex'],
                band_lower_unbounded=comparison['band_lower_unbounded'],
                band_upper_unbounded=comparison['band_upper_unbounded'],
                saturation_gap_dex=comparison['saturation_gap_dex'],
            )
    return state


def kress91_oxygen_demand_mol(
    *, feo_mol: float, fe2o3_mol: float, fO2_log: float,
    mol_fractions: Mapping[str, float], T_K: float, pressure_bar: float,
) -> float:
    """Finite O2 uptake (negative for release) needed to reach a gas potential."""
    target = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log, mol_fractions=mol_fractions,
        T_K=T_K, pressure_bar=pressure_bar,
    )
    # 2 FeO + 1/2 O2 = Fe2O3: n_Fe stays constant, n_Fe2O3=x*n_Fe/2,
    # so demand = (target n_Fe2O3 - current n_Fe2O3)/2 mol O2.
    # At equal splits demand=0; from all-ferrous to all-ferric it is n_Fe/4.
    return (0.5 * target * (feo_mol + 2.0 * fe2o3_mol) - fe2o3_mol) / 2.0


def split_feot_composition(
    composition_wt_pct: Mapping[str, float], T_K: float, *,
    redox_state: RedoxState | None = None,
    pressure_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> dict[str, object]:
    """Split canonical total-Fe-as-FeO input; callers canonicalize lab labels."""
    state = redox_state or RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.0)
    fO2_log = state.log_fO2_at(T_K)
    comp = dict(composition_wt_pct)
    if any(key in comp for key in ('FeOt', 'FeOT', 'FeO_total')):
        raise Kress91InvalidControls('canonical total Fe as FeO is required')
    if 'Fe2O3' in comp:
        raise Kress91InvalidControls('FeOt conversion requires an unsplit analysis')
    x = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log, T_K=T_K, pressure_bar=pressure_bar,
        mol_fractions=melt_mol_fractions_for_kress91(comp),
    )
    comp, factor = _split_feot_at_ferric_fraction(comp, x)
    return {
        'composition_wt_pct': comp, 'fO2_log': fO2_log,
        'renormalization_factor': factor,
        'fe3_over_sigma_fe': x,
        'condition': 'condition_stated' if redox_state is not None else 'condition_defaulted',
        'temperature_band': kress91_temperature_band_case(T_K - 273.15),
        'certification_gaps': [] if redox_state is not None else ['intrinsic_delta_iw'],
    }


def _split_feot_at_ferric_fraction(
    composition_wt_pct: Mapping[str, float], x: float,
) -> tuple[dict[str, float], float]:
    from simulator.state import MOLAR_MASS

    comp = dict(composition_wt_pct)
    feot = feot_equivalent_wt_pct(comp)
    # FeOt is analytical bookkeeping, not a declaration of zero ferric oxygen.
    # On a 100 g analysis basis n = FeOt/M_FeO mol Fe is conserved by
    # n_Fe2O3=x*n/2 and n_FeO=(1-x)*n. The hidden oxygen adds
    # x*n*(M_Fe2O3/2-M_FeO) grams; normalize the complete analysis by
    # 100/sum(corrected wt) to keep the stated batch mass, with no O2 import.
    # Units: mol*g/mol=g; the normalization factor is dimensionless.
    # At x=0 the split adds no oxygen; with no Fe it only normalizes the assay.
    # Lunar sanity (1673.15 K, IW-1): x=0.01817376108; the complete
    # feedstock scale is 1.02680810709 and Fe2O3 is 0.34218983917 wt%.
    n = feot / MOLAR_MASS['FeO']
    comp.update(FeO=(1.0 - x) * n * MOLAR_MASS['FeO'],
                Fe2O3=x * n * MOLAR_MASS['Fe2O3'] / 2.0)
    factor = 100.0 / sum(comp.values())
    normalize_component_masses_kg(comp, 100.0)
    return comp, factor


def collapse_feot_composition(
    composition_wt_pct: Mapping[str, float], *, include_metal: bool = False,
) -> dict[str, float]:
    """Return a normalized total-Fe-as-FeO assay for lab comparisons."""
    from simulator.state import MOLAR_MASS

    comp = dict(composition_wt_pct)
    feot = feot_equivalent_wt_pct(comp)
    comp.pop('Fe2O3', None)
    if include_metal:
        # One Fe atom in FeO: metal mass * M_FeO/M_Fe is FeOt mass.
        feot += comp.pop('Fe', 0.0) * MOLAR_MASS['FeO'] / MOLAR_MASS['Fe']
    comp.update(FeO=feot)
    normalize_component_masses_kg(comp, 100.0)
    return comp


@lru_cache(maxsize=1)
def _iron_class_priors() -> dict:
    return yaml.safe_load((Path(__file__).resolve().parent.parent /
                           'data/iron_class_priors.yaml').read_text())


def initialize_feedstock_iron(feedstock: Mapping[str, object]) -> tuple[dict, dict]:
    """Preserve declared iron splits; predict missing splits once, before seeding."""
    from simulator.state import MOLAR_MASS

    validate_iron_speciation(feedstock)
    prepared = dict(feedstock)
    basis = dict(feedstock.get('composition_basis', {}) or {})
    if 'iron_initialization' in basis:
        return prepared, dict(basis['iron_initialization'])
    comp = dict(feedstock.get('composition_wt_pct', {}))
    spec = dict(feedstock.get('iron_speciation', {}) or {})
    anchor = dict(feedstock.get('intrinsic_redox', {}) or {})
    T_K = float(anchor.get('formation_temperature_K', KRESS91_FO2_KEY_REFERENCE_T_K))
    ferric_observation = spec.get('fe3_over_sum_fe') or spec.get('fe2o3_wt_pct')
    authority = (IronSeedAuthority(ferric_observation['authority']) if ferric_observation
                 else IronSeedAuthority.MODEL_ASSUMED)
    declared_authority = IronSeedAuthority(basis.get('FeO', {}).get(
        'authority', 'measured' if all(basis.get(s, {}).get('source')
                                     for s in ('FeO', 'Fe2O3')) else 'model_assumed'))
    declared_split = 'Fe2O3' in comp
    if not spec and (declared_split or not iron_oxide_values(comp)[0]):
        authority = declared_authority
        return prepared, {
            'method': 'declared_split', 'formation_temperature_K': T_K,
            'seed_authority': authority,
            'certification_gaps': (['formation_temperature_K']
                if anchor.get('formation_temperature_estimated', True) else []),
        }
    metal_diagnostic = {}
    metal_wt_pct = 0.0
    metal_observation = spec.get('fe0_metal_wt_pct') or spec.get('is_over_feo')
    if metal_observation:
        if 'fe0_metal_wt_pct' in spec:
            metal_wt_pct = float(metal_observation['value'])
        else:
            metal_diagnostic['metal_conversion'] = nanophase_iron_from_fmr(
                float(spec['is_over_feo']['value']), float(spec['feo_wt_pct']['value']))
            metal_wt_pct = metal_diagnostic['metal_conversion']['fe0_metal_wt_pct']
        elemental = dict(prepared.get('elemental_composition_wt_pct', {}) or {})
        if 'Fe' in elemental:
            raise Kress91InvalidControls('iron_speciation Fe0 duplicates elemental Fe inventory')
        elemental['Fe'] = metal_wt_pct
        prepared['elemental_composition_wt_pct'] = elemental
        metal_diagnostic.update(
            metal_seed_authority=IronSeedAuthority(metal_observation['authority']),
            metal_phase_species=['Fe'], fe0_metal_wt_pct=metal_wt_pct,
        )
        alloy = metal_observation.get('metal_composition_wt_pct', {})
        additions = dict(prepared.get('bulk_additions', {}) or {})
        # Fe0 is elemental Fe mass. If Ni/Co mass shares y are measured in
        # that metal, M_alloy=M_Fe/(1-sum(y)); M_i=M_alloy*y_i. All are on
        # the same wt% basis. Transfer any declared elemental trace inventory
        # instead of counting it twice; absent trace rows are supplied by the
        # alloy assay. At y=0 the metal contains only Fe.
        total_metal = metal_wt_pct / (1.0-sum(alloy.values())/100.0)
        metal_diagnostic['metal_alloy_wt_pct'] = total_metal
        for species, pct in alloy.items():
            amount = total_metal * pct / 100.0
            if species in additions:
                raise Kress91InvalidControls(f'iron_speciation duplicates bulk {species}')
            if species in comp:
                available = _representative_number(comp[species], species)
                if available is None or amount > available:
                    raise Kress91InvalidControls(f'iron_speciation alloy exceeds bulk {species}')
                comp[species] = available - amount
            additions[species] = amount
            metal_diagnostic['metal_phase_species'].append(species)
        if alloy:
            prepared['bulk_additions'] = additions
        # The unsplit assay is TOTAL Fe as FeO, including measured metal.
        # n_Fe,oxide = FeOt/M_FeO - Fe0/M_Fe; replace that FeO-equivalent
        # mass before splitting, so adding the metal never invents Fe atoms.
        # wt%/g mol^-1 is mol/100g; M_FeO/M_Fe is dimensionless. At Fe0=0
        # the original assay is recovered. Full-feed normalization follows.
        if not declared_split and feot_equivalent_wt_pct(comp) > 0:
            feot = feot_equivalent_wt_pct(comp) - metal_wt_pct * MOLAR_MASS['FeO']/MOLAR_MASS['Fe']
            if feot <= 0:
                raise Kress91InvalidControls('iron_speciation Fe0 exhausts the total-Fe assay')
            comp.update(FeO=feot)
    stated = 'delta_iw' in anchor
    conversion = {}
    prior_diagnostic = {}
    if ferric_observation:
        if 'fe3_over_sum_fe' in spec:
            measured_q = float(ferric_observation['value'])
            oxide_fe = feot_equivalent_wt_pct(comp) / MOLAR_MASS['FeO']
            # The measured denominator is ALL Fe, including declared Fe0.
            # Kress uses oxide Fe only: q_oxide=q_measured*(n_oxide+n_0)/n_oxide.
            # Mol/mol cancels; n_0=0 recovers the measured fraction unchanged.
            if oxide_fe <= 0:
                raise Kress91InvalidControls('ferric iron_speciation requires oxide iron')
            q = measured_q * (oxide_fe + metal_wt_pct/MOLAR_MASS['Fe']) / oxide_fe
            if q > 1:
                raise Kress91InvalidControls('ferric iron_speciation plus Fe0 exceeds total Fe')
        else:
            feo = float(spec['feo_wt_pct']['value']) / MOLAR_MASS['FeO']
            ferric = 2 * float(spec['fe2o3_wt_pct']['value']) / MOLAR_MASS['Fe2O3']
            if feo + ferric <= 0:
                raise Kress91InvalidControls('printed iron_speciation pair has no iron')
            q = ferric / (feo + ferric)
        split, factor = _split_feot_at_ferric_fraction(comp, q)
        result = {'composition_wt_pct': split, 'renormalization_factor': factor,
                  'fe3_over_sigma_fe': q, 'condition': 'condition_stated',
                  'certification_gaps': []}
        state = None
    elif declared_split or feot_equivalent_wt_pct(comp) == 0:
        feo, ferric = iron_oxide_values(comp)
        n3 = 2*ferric/MOLAR_MASS['Fe2O3']
        total = feo/MOLAR_MASS['FeO'] + n3
        result = {'composition_wt_pct': comp, 'renormalization_factor': 1.0,
                  'fe3_over_sigma_fe': n3/total if total else 0.0,
                  'condition': 'condition_stated', 'certification_gaps': []}
        authority = declared_authority
        state = None
    else:
        prior_class = feedstock.get('iron_redox_class') if not stated else None
        if prior_class is not None:
            if prior_class not in _iron_class_priors():
                raise Kress91InvalidControls(f'unknown iron redox class {prior_class!r}')
            anchor = {**_iron_class_priors()[prior_class], **anchor,
                      'authority': 'model_assumed', 'estimated': True,
                      'buffer_source': {'reference': CONVENTIONAL_IW_SOURCE, 'page': 1067}}
            delta = anchor['delta_iw']
            low, high = anchor['interval']
            prior_diagnostic = {
                'seed_basis': 'class_prior', 'class_prior': prior_class,
                'prior_half_width_dex': max(delta-low, high-delta),
            }
        state, conversion = _feedstock_anchor_state(anchor, T_K, class_prior=bool(prior_class))
        result = split_feot_composition(comp, T_K, redox_state=state)
        authority = IronSeedAuthority(anchor.get('authority', 'model_assumed'))
    # Preserve the original raw-analysis basis including nonoxide constituents;
    # the existing complete-feedstock normalizer scales every mass bucket once.
    prepared['composition_wt_pct'] = {
        oxide: value / result['renormalization_factor']
        for oxide, value in result['composition_wt_pct'].items()
    }
    # This internal prepared analysis is predicted, never exposed as a measured
    # assay: retain the original metadata on the simulator and in the diagnostic.
    prepared['fe_redox_split_unknown'] = False
    for oxide in ('FeO', 'Fe2O3'):
        if declared_split and not ferric_observation:
            continue
        basis[oxide] = {
            'method': ('iron_speciation initialization' if ferric_observation or declared_split
                       else 'predicted Kress91 initialization'),
            'source': str(ferric_observation['source'] if ferric_observation
                          else anchor.get('source', 'predicted IW default')),
            'authority': authority.value,
        }
    prepared['composition_basis'] = basis
    stage0 = dict(feedstock.get('anhydrous_silicate_after_degassing', {}) or {})
    if stage0:
        stage0_comp = stage0.get('composition_wt_pct', {})
        if 'Fe2O3' not in stage0_comp and iron_oxide_values(stage0_comp)[0]:
            stage0['composition_wt_pct'] = (
                _split_feot_at_ferric_fraction(stage0_comp, result['fe3_over_sigma_fe'])[0]
                if ferric_observation or declared_split else split_feot_composition(
                    stage0_comp, T_K, redox_state=state)['composition_wt_pct'])
        prepared['anhydrous_silicate_after_degassing'] = stage0
    gaps = list(result['certification_gaps'])
    if not ferric_observation and stated and anchor.get('estimated', True):
        gaps.append('intrinsic_delta_iw')
    if 'formation_temperature_K' not in anchor or anchor.get('formation_temperature_estimated', True):
        gaps.append('formation_temperature_K')
    if authority is not IronSeedAuthority.MEASURED:
        gaps.append('measured_feedstock_iron_split')
    if prior_diagnostic:
        gaps.append('class_prior_unmeasured')
    # Retain computed provenance on this private prepared analysis. Re-loading
    # it must not repeat a total-Fe subtraction or lose its metal account route.
    diagnostic = {
        **result, **conversion, **metal_diagnostic,
        **prior_diagnostic,
        **({'iron_speciation': spec} if spec else {}),
        'method': ('iron_speciation_split' if ferric_observation else
                   'declared_split' if declared_split else 'predicted_kress91_split'),
        'seed_authority': authority,
        'condition': ('condition_defaulted' if not ferric_observation and not declared_split
                      and anchor.get('estimated', True) else result['condition']),
        'formation_temperature_K': T_K, 'anchor': anchor, 'certification_gaps': gaps,
    }
    basis['iron_initialization'] = diagnostic
    complete = normalized_feedstock_component_masses_kg(prepared, 100.0)
    # The normalized Si/Fe proportions carry the whole-assay scale, including
    # native metal and nonoxide masses; use a nonzero corrected oxide as probe.
    analysis = dict(prepared['composition_wt_pct'])
    if metal_observation:
        analysis['Fe'] = metal_wt_pct
    probe = next(k for k, v in analysis.items() if v > 0.0)
    factor = complete[probe] / analysis[probe]
    # The cleanup dose is per tonne of the original complete analysis. If its
    # mass is A and ferric oxygen adds dA, sulfate and its stoichiometric C dose
    # both scale by A/(A+dA). Since factor=100/(A+dA), this is 1-factor*dA/100.
    # All masses use the same 100 g analysis basis; the ratio is dimensionless
    # and becomes one when no oxygen is added, even when A is not 100 g.
    added_oxygen = (sum(prepared['composition_wt_pct'].values()) - sum(
        feedstock.get('composition_wt_pct', {}).values())
        + float(metal_diagnostic.get('metal_alloy_wt_pct', 0.0)))
    dose_scale = 1.0 - factor * added_oxygen / 100.0
    cleanup = prepared.get('stage0_carbon_cleanup')
    if isinstance(cleanup, Mapping):
        prepared['stage0_carbon_cleanup'] = dict(cleanup)
    for declaration, key in (
        (prepared, 'stage0_carbon_reductant_kg_per_tonne'),
        (prepared.get('stage0_carbon_cleanup'), 'carbon_reductant_kg_per_tonne'),
    ):
        if not isinstance(declaration, dict):
            continue
        value = declaration.get(key)
        if is_declared_real_scalar(value) and isinstance(value, (int, float)):
            declaration[key] = float(value) * dose_scale
        elif isinstance(value, (list, tuple)) and len(value) == 2 and all(
            is_declared_real_scalar(v, allow_numeric_str=True) for v in value
        ):
            declaration[key] = [float(v) * dose_scale for v in value]
    diagnostic['renormalization_factor'] = factor
    return prepared, diagnostic


def _feedstock_anchor_state(
    anchor: Mapping, T_K: float, *, class_prior: bool = False,
) -> tuple[RedoxState | None, dict]:
    if 'delta_iw' not in anchor:
        raise Kress91InvalidControls(
            'unsplit feedstock iron requires cited intrinsic_redox delta_iw, buffer and interval')
    delta = _redox_state_finite_float(anchor['delta_iw'], 'intrinsic delta_iw')
    buffer = anchor.get('buffer')
    if buffer is None:
        raise Kress91InvalidControls('intrinsic_redox requires an explicit cited buffer')
    if buffer not in {'pure_liquid_wustite', FE_FEO_BUFFER_STANDARD, CONVENTIONAL_IW_BUFFER}:
        raise Kress91InvalidControls(f'unsupported intrinsic_redox buffer {buffer!r}')
    interval = anchor.get('interval')
    if (not isinstance(interval, (list, tuple)) or len(interval) != 2
            or not all(is_declared_real_scalar(v) and isinstance(v, (float, int))
                       and math.isfinite(v) for v in interval)
            or not interval[0] <= delta <= interval[1]):
        raise Kress91InvalidControls('intrinsic_redox requires delta_iw within a cited interval')
    source = anchor.get('buffer_source', {})
    locator = anchor.get('locator', {})
    if (not isinstance(source, Mapping) or not source.get('reference') or not source.get('page')
            or not anchor.get('source') or not isinstance(locator, Mapping)
            or not (class_prior and anchor.get('source') == 'class prior, no corpus citation')
            and not any(locator.get(k) is not None for k in ('published_page', 'page', 'pdf_page_index'))):
        raise Kress91InvalidControls('intrinsic_redox buffer and delta_iw require sources with page')
    if buffer in {'pure_liquid_wustite', FE_FEO_BUFFER_STANDARD}:
        return RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, delta), {}
    iw = conventional_iw_log10_fO2_bar(T_K)
    # Convert the absolute potential ONCE; no T-dependent offset is retained
    # in the initialized inventory. delta_liquid=(IW_s+delta_s)-IW_l, dex.
    # Both references use bar, so delta=0 on the literature scale recovers
    # conventional IW, not the different pure-liquid reference.
    value = iw + delta
    return RedoxState.absolute(value), {'buffer_conversion': {
        'buffer': buffer, 'source': CONVENTIONAL_IW_SOURCE,
        'reference_uncertainty_note': (
            'Conventional IW expressions span about 0.14 dex at 1673 K '
            '(Sato-Frost; Sato-O\'Neill & Pownceby 1993 about 0.13 dex; '
            'see Hirschmann 2021, GCA 313:74-84). This is comparable to the '
            '0.15 dex solid-to-liquid reference shift and much smaller than '
            'the cited 1.5 dex lunar proxy interval; no buffer value is adjusted.'),
        'literature_iw_log': iw, 'liquid_iw_log': feo_iw_log10_fO2_bar(T_K),
        'delta_iw_liquid': value - feo_iw_log10_fO2_bar(T_K),
        'fO2_interval_log': [iw + interval[0], iw + interval[1]],
        'extrapolated': not 1123.15 <= T_K <= 1573.15,
    }}


def nanophase_iron_from_fmr(is_over_feo: float, feo_wt_pct: float) -> dict:
    # Escobar-Cerezo et al. 2018 ApJ 853:71 p.5 Eq.6 (Morris1980):
    # npFe0/FeO=(3.20+/-0.08)e-4*(Is/FeO)+(1+/-5)e-4, a mass ratio.
    # Multiplying by source-sample FeO wt% gives Fe0 wt%, NOT a fraction
    # needing another /100. Is=12, FeO=17.6 wt% gives .069344 wt%.
    # This calibration counts 4-33nm metal; coarser metal is unmeasured.
    return {'fe0_metal_wt_pct': feo_wt_pct * (.000320 * is_over_feo + .0001),
            'source': 'Escobar-Cerezo et al. 2018, doi:10.3847/1538-4357/aaa24d, p.5 Eq.6',
            'scope': '4-33 nm npFe0 only; coarser native/meteoritic metal unconstrained'}


def fe0_comproportionation_extent(
    fe_mol: float, fe2o3_mol: float, *, feo_mol: float,
    comp_wt: Mapping[str, float], T_K: float, pressure_bar: float,
) -> dict:
    from scipy.optimize import brentq
    from simulator.state import MOLAR_MASS

    # Fe0 + Fe2O3 -> 3 FeO gives F'=F+3n, C'=C-n, oxide Fe I'=I+n.
    # On the Kress basis S'=N+I+n, where N is non-Fe oxide moles. At
    # metal contact a=gamma(T)*F'/S' (capped at 1), and equilibrium requires
    # q=2C'/(F'+2C') = q_Kress(IW(a)). Reuse the activity/forward owners.
    # To see uniqueness, let g=a_K*ln(10)*(f_inverse-IW(a)). On the uncapped
    # limb g'=-1/C'-(3+6a_K)/F'+2a_K/S'+D/S'^2, with
    # D=sum((c_i+1.828)*N_i)<=8.043*N, from the existing Kress coefficients.
    # For y=F'/S', S'g' < -4.176/y+.392+8.043*(1-y) < 0:
    # its maximum is 8.435-2*sqrt(4.176*8.043) < 0. On the capped
    # limb the corresponding bound -3/y+8.043*(1-y) is also negative.
    # Hence there is at most one crossing; bracket it by reactant exhaustion.
    # n, F, C, S are mol; g is dimensionless and g' is mol^-1. No reactant
    # means zero extent; insufficient metal is fully consumed; ferric-surplus
    # contact retains q_Kress rather than forcing an all-ferrous inventory.
    high = min(fe_mol, fe2o3_mol)
    if high <= 0.0:
        return {'extent_mol': 0.0, 'feo_credit_mol': 0.0,
                'metal_remaining_mol': fe_mol, 'fe2o3_remaining_mol': fe2o3_mol,
                'equilibrium_gap_dex': None}
    feo_w, ferric_w = iron_oxide_values(comp_wt)
    basis = ((feo_mol*MOLAR_MASS['FeO']/feo_w) if feo_mol > 0.0 else
             fe2o3_mol*MOLAR_MASS['Fe2O3']/ferric_w)

    def contact(n):
        comp = dict(comp_wt)
        comp.update(FeO=(feo_mol+3*n)*MOLAR_MASS['FeO']/basis,
                    Fe2O3=(fe2o3_mol-n)*MOLAR_MASS['Fe2O3']/basis)
        normalize_component_masses_kg(comp, 100.0)
        q = 2*(fe2o3_mol-n)/(feo_mol+2*fe2o3_mol+n)
        # Any metal-saturation potential is <= pure IW: selecting that
        # endpoint calls the same owner's metal-contact gamma-X limb.
        activity = calphad_ferrous_feo_activity_diagnostic(
            comp_wt=comp, fe3_over_sigma_fe=q, T_K=T_K,
            pressure_bar=pressure_bar, fO2_log=feo_iw_log10_fO2_bar(T_K))
        bound = _feo_activity_saturation_log10_fO2_bar(
            T_K, a_feo=float(activity['a_FeO_authoritative']))
        target = kress91_fe3_over_sigma_fe(
            fO2_log=bound, mol_fractions=melt_mol_fractions_for_kress91(comp),
            T_K=T_K, pressure_bar=pressure_bar)
        return q-target, q, target

    if contact(0.0)[0] <= 0.0:
        n = 0.0
    elif contact(high)[0] >= 0.0:
        n = high
    else:
        n = brentq(lambda x: contact(x)[0], 0.0, high,
                   xtol=1e-12, rtol=1e-14)
    _, q, target = contact(n)
    gap = (math.log10((q/(1-q))/(target/(1-target))) /
           KRESS91_LN_FO2_COEFFICIENT) if 0 < q < 1 and 0 < target < 1 else None
    return {'extent_mol': n, 'feo_credit_mol': 3.0*n,
            'metal_remaining_mol': fe_mol-n, 'fe2o3_remaining_mol': fe2o3_mol-n,
            'equilibrium_gap_dex': gap}


def _kress91_exchange_uptake_mol(
    *, feo_mol: float, fe2o3_mol: float,
    mol_fractions: Mapping[str, float], T_K: float, pressure_bar: float,
    head_o2_mol: float, available_o2_mol: float,
    gas_log_fO2: Callable[[float], float],
) -> float:
    """Finite closed-headspace equilibrium transfer, positive into liquid."""
    def demand(uptake: float) -> float:
        return kress91_oxygen_demand_mol(
            feo_mol=feo_mol, fe2o3_mol=fe2o3_mol,
            fO2_log=gas_log_fO2(max(0.0, head_o2_mol - uptake)),
            mol_fractions=mol_fractions, T_K=T_K, pressure_bar=pressure_bar,
        )

    initial = demand(0.0)
    low, high = (0.0, min(initial, available_o2_mol)) if initial > 0.0 else (initial, 0.0)
    # This solves oxygen CONSERVATION against the changing gas, not the Kress
    # inverse (which remains closed form). For uptake u, n_gas'=n_gas-u and
    # n_Fe2O3'=n_Fe2O3+2u. Equilibrium demands D(pO2(n_gas-u))=u mol O2.
    # D decreases with u, so the bracket is monotone; at gas exhaustion the
    # actual available molecules bound uptake. Multiplying this finite answer
    # by the kinetic relaxation cannot overshoot either equilibrium or stock.
    if initial > 0.0 and demand(high) >= high:
        return high
    for _ in range(64):
        mid = (low + high) / 2.0
        if demand(mid) > mid:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _calphad_authority_weight(delta_iw_log10: float) -> float:
    if delta_iw_log10 <= 0.0:
        return 1.0
    if delta_iw_log10 >= CALPHAD_AUTHORITY_BLEND_WIDTH_LOG10:
        return 0.0
    return 1.0 - delta_iw_log10 / CALPHAD_AUTHORITY_BLEND_WIDTH_LOG10


def kress91_ferrous_feo_activity(
    *,
    comp_wt: Mapping[str, float],
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    components = _calphad_feo_activity_components(
        comp_wt=comp_wt,
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
        floor_bar=floor_bar,
    )
    return max(0.0, float(components.get('a_FeO_authoritative', 0.0) or 0.0))


def _calphad_feo_activity_components(
    *,
    comp_wt: Mapping[str, float],
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
    fe3_over_sigma_fe: float | None = None,
) -> dict[str, object]:
    feot = feot_equivalent_wt_pct(comp_wt)
    mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
    split = None
    if feot <= 0.0 or not mol_fractions:
        kress91_activity = 0.0
    else:
        pressure_control = floor_vacuum_pressure_bar(
            pressure_bar,
            floor_bar=floor_bar,
        )
        split = kress91_split(
            fO2_log=fO2_log,
            mol_fractions=mol_fractions,
            T_K=T_K,
            pressure_bar=pressure_control,
        )
        if fe3_over_sigma_fe is not None:
            x = float(fe3_over_sigma_fe)
            if not math.isfinite(x) or not 0.0 <= x <= 1.0:
                raise Kress91InvalidControls('activity ferric fraction must be finite in [0, 1]')
            # In the closed state the ledger already specifies the split.
            # n_FeO=(1-x)*n_Fe and n_Fe2O3=x*n_Fe/2; no second Kress state.
            # At x=1, FeO activity is zero and Fe2O3/FeO has no finite value.
            split.update(fe3=x, ratio=x / (2.0 * (1.0-x)) if x < 1.0 else None,
                         x_feo=(1.0-x)*mol_fractions['FeOt'],
                         x_fe2o3=x*mol_fractions['FeOt']/2.0)
        kress91_activity = max(0.0, float(split['x_feo']))
    if not mol_fractions or mol_fractions.get('FeOt', 0.0) <= 0.0:
        return {
            'status': 'unavailable',
            'reason': 'no_FeOt_melt_component',
            'diagnostic_only': False,
            'consumed_by_behavior': False,
            'authority_unchanged': True,
            'a_FeO_current': kress91_activity,
            'a_FeO_kress91': kress91_activity,
            'a_FeO_authoritative': kress91_activity,
            'sources': FEO_ACTIVITY_DIAGNOSTIC_SOURCES,
        }

    pressure_control = floor_vacuum_pressure_bar(
        pressure_bar,
        floor_bar=floor_bar,
    )
    if split is None:
        split = kress91_split(
            fO2_log=fO2_log,
            mol_fractions=mol_fractions,
            T_K=T_K,
            pressure_bar=pressure_control,
        )
    x_feo_ferrous = max(0.0, float(split.get('x_feo', 0.0) or 0.0))
    cation_fractions = _melt_cation_fractions(
        comp_wt,
        fe3_over_sigma_fe=float(split.get('fe3', 0.0) or 0.0),
    )
    holzheid = holzheid_stoich_feo_gamma_band(T_K)
    banya = ban_ya_quadratic_gamma_feo(cation_fractions, T_K=T_K)
    gamma_banya = float(banya.get('gamma', 1.0) or 1.0)
    gamma_central = float(holzheid['central'])
    gamma_measurement_low = float(holzheid['measurement_low'])
    gamma_measurement_high = float(holzheid['measurement_high'])
    gamma_low = max(0.0, min(1.0, gamma_banya, gamma_measurement_low))
    gamma_high = max(gamma_measurement_high, gamma_central, gamma_banya)

    activity = {
        'low': x_feo_ferrous * gamma_low,
        'central': x_feo_ferrous * gamma_central,
        'high': x_feo_ferrous * gamma_high,
    }
    central = float(activity['central'])
    low = float(activity['low'])
    high = float(activity['high'])
    if kress91_activity > 0.0:
        ratio_kress91 = central / kress91_activity
        delta_log10_kress91 = (
            math.log10(ratio_kress91) if ratio_kress91 > 0.0 else None
        )
    else:
        ratio_kress91 = None
        delta_log10_kress91 = None
    delta_iw_shift_kress91 = (
        2.0 * delta_log10_kress91
        if delta_log10_kress91 is not None
        else None
    )
    pure_feo_iw = feo_iw_log10_fO2_bar(T_K, a_feo=1.0)
    delta_iw_pure_feo = float(fO2_log) - pure_feo_iw
    calphad_weight = _calphad_authority_weight(delta_iw_pure_feo)
    kress91_weight = 1.0 - calphad_weight
    authoritative_unclamped = calphad_weight * central + kress91_weight * kress91_activity
    # Premise: the pure-liquid-FeO standard state has a_FeO = 1 at metal
    # saturation, so greater activities are unphysical supersaturation.  The
    # min() therefore creates a continuous kink: below 1 the authority follows
    # the blend, while above 1 its slope is zero.  At the limiting value both
    # branches return exactly 1 (and, e.g., an unclamped 1.2 remains authority 1).
    authoritative = min(authoritative_unclamped, 1.0)
    if authoritative > 0.0:
        ratio_current = central / authoritative
        delta_log10_current = (
            math.log10(ratio_current) if ratio_current > 0.0 else None
        )
    else:
        ratio_current = None
        delta_log10_current = None
    delta_iw_shift_current = (
        2.0 * delta_log10_current
        if delta_log10_current is not None
        else None
    )
    if calphad_weight >= 1.0:
        regime = 'calphad_metal_saturated_below_iw_pure_feo'
    elif calphad_weight <= 0.0:
        regime = 'kress91_ferric_limb_above_iw_pure_feo_plus_1'
    else:
        regime = 'iw_pure_feo_to_iw_pure_feo_plus_1_smooth_blend'

    return {
        'status': 'ok',
        'diagnostic_only': False,
        'consumed_by_behavior': True,
        'authority_unchanged': False,
        'standard_state': 'stoichiometric_FeO_l',
        'a_FeO_current': authoritative,
        'a_FeO_authoritative': authoritative,
        'a_FeO_authoritative_unclamped': authoritative_unclamped,
        'a_FeO_kress91': kress91_activity,
        'a_FeO_calphad': activity,
        'a_FeO_pure_feo_ceiling': 1.0,
        'a_FeO_authoritative_clamped_to_pure_feo_ceiling': (
            authoritative_unclamped > 1.0
        ),
        'current_within_calphad_band': low <= authoritative_unclamped <= high,
        'comparison': {
            'status': (
                'ok' if ratio_current is not None else 'not_comparable_current_zero'
            ),
            'central_over_kress91': ratio_kress91,
            'central_over_current': ratio_current,
            'log10_central_over_kress91': delta_log10_kress91,
            'log10_central_over_current': delta_log10_current,
            'delta_iw_log10_shift_central_minus_kress91': (
                delta_iw_shift_kress91
            ),
            'delta_iw_log10_shift_central_minus_current': (
                delta_iw_shift_current
            ),
        },
        'authority': {
            'regime': regime,
            'iw_basis': 'IW(pure-FeO)',
            'relative_to_iw_pure_feo_log10': delta_iw_pure_feo,
            'calphad_weight': calphad_weight,
            'kress91_weight': kress91_weight,
            'blend_width_log10': CALPHAD_AUTHORITY_BLEND_WIDTH_LOG10,
            'central_band_is_authoritative': True,
            'low_high_band_is_diagnostic': True,
        },
        'x_FeO_ferrous': x_feo_ferrous,
        'kress91_split': {
            'fe3_over_sigma_fe': split['fe3'],
            'fe2o3_over_feo_molar': split['ratio'],
            'x_fe2o3': split['x_fe2o3'],
            'x_feo': split['x_feo'],
            'temperature_band_case': split['temperature_band_case'],
            'temperature_band_status': split['temperature_band_status'],
            'temperature_band_source': split['temperature_band_source'],
            'authoritative': split['authoritative'],
            'extrapolation': split['extrapolation'],
            'high_uncertainty': split['high_uncertainty'],
        },
        'gamma_FeO': {
            'low': gamma_low,
            'central': gamma_central,
            'high': gamma_high,
            'measurement_low': gamma_measurement_low,
            'measurement_high': gamma_measurement_high,
            'holzheid': holzheid,
            'banya_quadratic': banya,
            'oneill_eggins_subregular': _oneill_eggins_subregular_shape(
                cation_fractions
            ),
            'li_coexistence': {
                'status': 'not_solved_stepB',
                'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['li_coexistence'],
            },
        },
        'metal_saturation_tie_point': {
            # Blend weights above retain their declared wustite reference;
            # all coexistence tiepoints use the activity's stoichiometric one.
            'iw_pure_feo_log10_fO2_bar': (
                _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=1.0)
            ),
            'central_melt_metal_saturation_log10_fO2_bar': (
                _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=max(central, 1.0e-300))
            ),
            'current_melt_metal_saturation_log10_fO2_bar': (
                _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=max(authoritative, 1.0e-300))
            ),
            'kress91_melt_metal_saturation_log10_fO2_bar': (
                _feo_activity_saturation_log10_fO2_bar(T_K, a_feo=max(kress91_activity, 1.0e-300))
            ),
            'central_melt_saturation_offset_from_iw_pure_feo_log10_fO2': (
                2.0 * math.log10(max(central, 1.0e-300))
            ),
            'current_melt_saturation_offset_from_iw_pure_feo_log10_fO2': (
                2.0 * math.log10(max(authoritative, 1.0e-300))
            ),
            'kress91_melt_saturation_offset_from_iw_pure_feo_log10_fO2': (
                2.0 * math.log10(max(kress91_activity, 1.0e-300))
            ),
            'iw_basis_note': (
                'Coexistence axis is pure stoichiometric FeO(l), a_FeO=1, '
                'paired with dG_s=dG_w-RT ln(m). A melt with a_FeO<1 reaches '
                'a_Fe=1 lower by 2*log10(a_FeO_melt).'
            ),
            'source': FEO_ACTIVITY_DIAGNOSTIC_SOURCES['holzheid_dg_feo_l'],
        },
        'cation_fractions': cation_fractions,
        'sources': FEO_ACTIVITY_DIAGNOSTIC_SOURCES,
    }


def calphad_ferrous_feo_activity_diagnostic(
    *,
    comp_wt: Mapping[str, float],
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
    fe3_over_sigma_fe: float | None = None,
    closed_ledger: bool = False,
) -> dict[str, object]:
    if closed_ledger:
        from simulator.state import MOLAR_MASS

        feo, fe2o3 = iron_oxide_values(comp_wt)
        n_fe2 = feo / MOLAR_MASS['FeO']
        n_fe3 = 2.0 * fe2o3 / MOLAR_MASS['Fe2O3']
        # Closed inventory fixes x=n_Fe3/(n_Fe2+n_Fe3), independent of its
        # stability potential. Mass/M gives mol on the same analysis basis;
        # normalization cancels. At Fe2O3=0, x=0 even at an IW lower bound:
        # forwarding that bound through Kress would invent ferric inventory.
        fe3_over_sigma_fe = n_fe3 / (n_fe2 + n_fe3) if n_fe2 + n_fe3 else 0.0
    return _calphad_feo_activity_components(
        comp_wt=comp_wt,
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
        floor_bar=floor_bar,
        fe3_over_sigma_fe=fe3_over_sigma_fe,
    )


def kress91_split(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> dict[str, object]:
    ratio = _kress91_fe2o3_over_feo_molar(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    fe3 = 2.0 * ratio / (2.0 * ratio + 1.0)
    x_fe2o3 = ratio * mol_fractions['FeOt'] / (2.0 * ratio + 1.0)
    x_feo = max(0.0, mol_fractions['FeOt'] - 2.0 * x_fe2o3)
    temperature_band = kress91_temperature_band_case(float(T_K) - 273.15)
    notices = _kress91_temperature_notices(float(T_K) - 273.15)
    return {
        'fe3': fe3,
        'ratio': ratio,
        'x_fe2o3': x_fe2o3,
        'x_feo': x_feo,
        'temperature_band_case': temperature_band['case'],
        'temperature_band_status': temperature_band['status'],
        'temperature_band_source': temperature_band['source'],
        **({'notices': notices} if notices else {}),
        'authoritative': temperature_band['authoritative'],
        'extrapolation': temperature_band['extrapolation'],
        'high_uncertainty': temperature_band['high_uncertainty'],
    }


def _kress91_fe3_fe2_ratio(fe2o3_over_feo_molar: float) -> float:
    # One Fe2O3 formula unit contains two Fe3+ cations; FeO contains one
    # Fe2+ cation, so Fe3+/Fe2+ = 2*n(Fe2O3)/n(FeO) = 2*ratio.
    return 2.0 * float(fe2o3_over_feo_molar)
