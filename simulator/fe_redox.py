from __future__ import annotations

import math
from collections.abc import Mapping

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR


class Kress91InvalidControls(ValueError):
    """Invalid finite-control input for the Kress91 Fe-redox relation."""


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
    if reference_T_K is None:
        return redox_fO2_log
    redox_reference_T_K = float(reference_T_K)
    redox_target_T_K = float(target_T_K)
    # 273.15 K is the exact Celsius-to-kelvin offset, so this is the
    # documented 1200 C liquid-calibration floor expressed in kelvin.
    calibration_min_T_K = KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15
    for name, temperature_K in (
        ('reference_T_K', redox_reference_T_K),
        ('target_T_K', redox_target_T_K),
    ):
        if temperature_K < calibration_min_T_K:
            raise Kress91InvalidControls(
                f'Kress91 invalid control {name}: {temperature_K!r} K is '
                'below liquid calibration floor '
                f'{calibration_min_T_K!r} K'
            )
    delta_ln_fO2 = kress91_ln_fO2_temperature_delta(
        redox_reference_T_K,
        redox_target_T_K,
        reference_pressure_bar=reference_pressure_bar,
        target_pressure_bar=target_pressure_bar,
    )
    return (redox_fO2_log * math.log(10.0) + delta_ln_fO2) / math.log(10.0)


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


def feot_equivalent_wt_pct(comp_wt: Mapping[str, float]) -> float:
    feo = max(0.0, float(comp_wt.get('FeO', 0.0) or 0.0))
    fe2o3 = max(0.0, float(comp_wt.get('Fe2O3', 0.0) or 0.0))
    return feo + fe2o3 * (2.0 * 71.844 / 159.687)


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
    activity = max(float(a_feo), 1.0e-300)
    R = 8.31446261815324
    ln_fO2 = 2.0 * (
        math.log(activity)
        + holzheid_feo_liquid_delta_g_j_mol(T_K) / (R * float(T_K))
    )
    return ln_fO2 / math.log(10.0)


def _alpha_j(cation_a: str, cation_b: str) -> float | None:
    if cation_a == cation_b:
        return 0.0
    return BAN_YA_ALPHA_J.get(frozenset((cation_a, cation_b)))


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

    for oxide in (*KRESS91_MOL_FRACTION_OXIDES, 'FeO', 'Fe2O3'):
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


def _kress91_ferrous_feo_activity_raw(
    *,
    comp_wt: Mapping[str, float],
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
    floor_bar: float = DEFAULT_VACUUM_FLOOR_BAR,
) -> float:
    feot = feot_equivalent_wt_pct(comp_wt)
    if feot <= 0.0:
        return 0.0
    mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
    if not mol_fractions:
        return 0.0
    # Vacuum tolerance — intentional, NOT a missing guard. Direct activity
    # callers may pass pressure_bar == 0.0 at furnace vacuum. Kress91's pressure
    # terms are a high-pressure (GPa) petrologic correction, negligible at
    # furnace mbar pressures, so a non-positive pressure is floored here rather
    # than refused. Vapor-equilibrium providers use
    # kress91_furnace_activity_pressure_bar so neutral overhead p_total never
    # enters this path. NON-FINITE pressure is deliberately left unfloored
    # (isfinite gate) so NaN/inf still raises through the
    # _validate_kress91_controls chokepoint.
    # kress91_split, by contrast, serves the redox-split path where pressure is a
    # real melt pressure > 0 and a non-positive value IS invalid — the two entry
    # points have DIFFERENT valid-input domains, so this asymmetry is correct, not
    # a class-incompleteness. (A prior fold removed this clamp on that mistaken
    # premise and broke every vacuum evaporation golden — see test
    # test_kress91_ferrous_feo_activity_vacuum_pressure_is_floored_not_refused.)
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
    # Kress & Carmichael 1991 uses oxide mole fractions; Holzheid et al. 1997
    # Eq. (4) defines gamma_FeO on the X_FeO mole-fraction basis.
    return max(0.0, float(split['x_feo']))


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
) -> dict[str, object]:
    kress91_activity = _kress91_ferrous_feo_activity_raw(
        comp_wt=comp_wt,
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
        floor_bar=floor_bar,
    )
    mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
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
            'iw_pure_feo_log10_fO2_bar': pure_feo_iw,
            'central_melt_metal_saturation_log10_fO2_bar': (
                feo_iw_log10_fO2_bar(T_K, a_feo=max(central, 1.0e-300))
            ),
            'current_melt_metal_saturation_log10_fO2_bar': (
                feo_iw_log10_fO2_bar(T_K, a_feo=max(authoritative, 1.0e-300))
            ),
            'kress91_melt_metal_saturation_log10_fO2_bar': (
                feo_iw_log10_fO2_bar(T_K, a_feo=max(kress91_activity, 1.0e-300))
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
                'IW axis is pure FeO(l), a_FeO=1. A melt with a_FeO<1 reaches '
                'a_Fe=1 at log10(fO2) lower by 2*log10(a_FeO_melt); the '
                'self-consistent melt-a_FeO saturation anchor is deferred.'
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
) -> dict[str, object]:
    return _calphad_feo_activity_components(
        comp_wt=comp_wt,
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
        floor_bar=floor_bar,
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
    return {
        'fe3': fe3,
        'ratio': ratio,
        'x_fe2o3': x_fe2o3,
        'x_feo': x_feo,
        'temperature_band_case': temperature_band['case'],
        'temperature_band_status': temperature_band['status'],
        'temperature_band_source': temperature_band['source'],
        'authoritative': temperature_band['authoritative'],
        'extrapolation': temperature_band['extrapolation'],
        'high_uncertainty': temperature_band['high_uncertainty'],
    }
