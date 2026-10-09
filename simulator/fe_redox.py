from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.physical_constants import (
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)
from simulator.feedstock_composition import (
    FEOT_FROM_FE2O3,
    OXYGEN_IN_FEO,
    FeRedoxPrior,
    feot_equivalent_wt_pct,
    iron_oxide_values,
)


class Kress91InvalidControls(ValueError):
    """Invalid finite-control input for the Kress91 Fe-redox relation."""


def melt_fO2_seed_without_ferric_iron(
    composition_wt_pct: Mapping[str, float],
) -> bool:
    """True when the seed has no Fe2O3 term to move it off the IW buffer."""

    _feo, fe2o3 = iron_oxide_values(composition_wt_pct)
    return fe2o3 <= 0.0


def intrinsic_melt_fO2(
    composition_wt_pct: Mapping[str, float],
    temperature_K: float,
    *,
    fe_redox_prior: FeRedoxPrior | None = None,
) -> float:
    """Melt oxygen potential adopted from the iron-wüstite buffer.

    Premise: before a liquid redox step has a reference temperature, the
    melt potential is the pure-FeO IW buffer at the temperature where it
    is adopted, plus the alkali offset already used by this seed. The
    ferric term applies only when both FeO and Fe2O3 are present and no
    prior is seated. Fe2O3 absent does not invent an IW-1 offset; the
    caller carries ``melt_fO2_seed_without_ferric_iron``.

    A delta_iw prior replaces that sum. Sato's offset is already the
    measured log10(fO2) minus production IW, so alkali is not added again.
    A measured Fe3+/sum-Fe prior in (0, 1) inverts Kress91 at this
    temperature (``kress91_fO2_log_for_fe3_fraction``); Kress91 already
    carries the Na2O and K2O terms, so alkali is not added on top.
    Fractions of exactly 0 or 1 have no finite Kress91 root. They seed
    the melt-dissociation envelope (``measured_fe3_fraction_seed``),
    not this IW-plus-alkali fallthrough.

    Algebra, no prior: log10(fO2/bar) = feo_iw_log10_fO2_bar(T) + redox_offset.
    feo_iw_log10_fO2_bar is Holzheid, Palme & Chakraborty 1997 liquid FeO
    at a_FeO = 1 (ΔG = -244118 + 115.559 T - 8.474 T ln T J/mol;
    ln(fO2) = 2 ΔG / (R T); log10 = ln / ln(10)). Alkali offset is
    min(0.15, (Na2O + K2O) wt% * 0.01) dex. Ferric offset, when both
    oxides are positive and no prior is seated, is 0.25 * log10(Fe2O3/FeO).
    delta_iw: log10(fO2/bar) = feo_iw_log10_fO2_bar(T) + prior.value.

    Units: T in K, ΔG in J/mol, R in J/(mol·K), prior.value in dex for
    delta_iw and dimensionless Fe3+/sum-Fe for a measured fraction.
    Result is dimensionless log10(fO2/bar).

    Sanity: Holzheid IW is -13.3608 at 1338 K (4.36e-14 bar) and -10.0490
    at 1638 K (8.93e-11 bar). At 1800 K it is -8.731 (1.86e-9 bar). A
    delta_iw of -1.01 at 1673.15 K is -9.740754 - 1.01 = -10.750754.
    The headspace vacuum floor is not applied here. Vapour mass action still
    uses the 1e-30..100 bar melt-dissociation envelope
    (MELT_DISSOCIATION_PO2_MIN_BAR / MAX_BAR).

    The 0.25 ferric coefficient is not a Kress91 inversion. Kress91's
    a = 0.196 would be ~5.1 per log10 molar ratio, and it takes a molar
    ratio rather than this weight ratio. It remains only on the no-prior
    path. A seated prior uses the forward Kress91 relation, inverted once
    in ``kress91_fO2_log_for_fe3_fraction``.
    """

    temperature = float(temperature_K)
    if fe_redox_prior is not None and fe_redox_prior.kind == "delta_iw":
        return feo_iw_log10_fO2_bar(temperature, a_feo=1.0) + float(
            fe_redox_prior.value
        )
    if (
        fe_redox_prior is not None
        and fe_redox_prior.kind == "measured_fe3_fraction"
    ):
        fO2_log, _domain = measured_fe3_fraction_seed(
            fe3_fraction=float(fe_redox_prior.value),
            composition_wt_pct=composition_wt_pct,
            T_K=temperature,
            pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
        )
        return fO2_log
    feo, fe2o3 = iron_oxide_values(composition_wt_pct)
    alkali = max(0.0, float(composition_wt_pct.get("Na2O", 0.0))) + max(
        0.0, float(composition_wt_pct.get("K2O", 0.0))
    )
    log_iw = feo_iw_log10_fO2_bar(temperature, a_feo=1.0)
    redox_offset = 0.0
    if fe_redox_prior is None and feo > 0.0 and fe2o3 > 0.0:
        redox_offset += 0.25 * math.log10(max(fe2o3 / feo, 1.0e-12))
    redox_offset += min(0.15, alkali * 0.01)
    return log_iw + redox_offset


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
# A measured Fe3+/sum-Fe prior is bulk speciation (XANES, mineralogy, or
# an oxide pair), not a liquid-equilibrium determination. The seed still
# inverts the liquid relation. The notice names this reason and does not
# refuse the run.
BULK_ROCK_ON_LIQUID_RELATION_REASON = "bulk_rock_fraction_on_liquid_relation"
KRESS91_AITHALA_EXPERIMENTAL_CONFIRMATION_MAX_T_C = 2100.0
KRESS91_HIGH_UNCERTAINTY_MAX_T_C = 2500.0
# 1400 C cache-label convention for isochemical redox keys, not new physics.
KRESS91_FO2_KEY_REFERENCE_T_K = 1673.15
# Load-time mass split pressure. Kress91 pressure terms are GPa corrections.
# At 1e5 Pa and 1673 K, KRESS91_PRESSURE_INV_T_COEFFICIENT * 1e5 / 1673 is
# about -4e-5 in ln(Fe2O3/FeO). 1.0 bar keeps the split defined
# (_validate_kress91_controls refuses a non-positive pressure) without
# pretending the batch has a GPa load.
LOAD_FE_SPLIT_PRESSURE_BAR = 1.0


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


def kress91_fO2_log_for_fe3_fraction(
    *,
    fe3_fraction: float,
    composition_wt_pct: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    """Invert the existing Kress91 forward ratio for log10(fO2/bar).

    Premise: this module had no inversion. The fO2 term inside
    ``_kress91_fe2o3_over_feo_molar`` is linear, ``a * fO2_log * ln(10)``
    with ``a = KRESS91_LN_FO2_COEFFICIENT``, and no other term depends on
    fO2. One call of that forward function at fO2_log = 0 supplies B =
    ln(ratio_0). The composition terms are not copied here.

    Algebra: fe3 = 2r / (2r + 1) with r = Fe2O3/FeO molar, so
    r = f / (2 (1 - f)). ln(r) = B + a * fO2_log * ln(10), hence
    fO2_log = (ln(r) - B) / (a * ln(10)).

    f of 0 or 1 makes r zero or infinite, so this relation has no finite
    fO2 there. The load path passes ``LOAD_FE_SPLIT_PRESSURE_BAR``.

    Units: f is Fe3+/sum-Fe, T_K kelvin, pressure_bar bar, result
    log10(fO2/bar).

    Sanity: ``kress91_fe3_over_sigma_fe`` at the returned log reproduces f.
    """

    fraction = float(fe3_fraction)
    if not math.isfinite(fraction) or fraction <= 0.0 or fraction >= 1.0:
        raise Kress91InvalidControls(
            "Kress91 inversion needs a ferric fraction strictly between 0 and 1"
        )
    mol_fractions = melt_mol_fractions_for_kress91(composition_wt_pct)
    if not mol_fractions or float(mol_fractions.get("FeOt", 0.0)) <= 0.0:
        raise Kress91InvalidControls(
            "Kress91 inversion needs a positive FeOt mole fraction"
        )
    ratio_at_zero = _kress91_fe2o3_over_feo_molar(
        fO2_log=0.0,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    if ratio_at_zero <= 0.0 or not math.isfinite(ratio_at_zero):
        raise Kress91InvalidControls(
            "Kress91 forward ratio at log10(fO2) = 0 is not positive"
        )
    target_ratio = fraction / (2.0 * (1.0 - fraction))
    return (math.log(target_ratio) - math.log(ratio_at_zero)) / (
        KRESS91_LN_FO2_COEFFICIENT * math.log(10.0)
    )


MEASURED_FRACTION_SEED_KRESS91 = "kress91"
MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE = "melt_dissociation_envelope"


def measured_fe3_fraction_seed_domain(fe3_fraction: float) -> str:
    """Which relation seeds a measured Fe3+/sum-Fe fraction.

    Kress91's molar ratio r = f / (2 (1 - f)) is zero at f = 0 and
    infinite at f = 1, so the inversion has no finite log10(fO2).
    Those two endpoints use the melt-dissociation envelope. Every other
    fraction, including values the parser will later refuse, is the
    Kress91 domain; the inversion itself still rejects f outside (0, 1).
    """

    fraction = float(fe3_fraction)
    if fraction == 0.0 or fraction == 1.0:
        return MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE
    return MEASURED_FRACTION_SEED_KRESS91


def measured_fe3_fraction_seed(
    *,
    fe3_fraction: float,
    composition_wt_pct: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> tuple[float, str]:
    """log10(fO2/bar) for a measured ferric fraction, and its domain.

    Premise: an interior fraction inverts Kress91 once
    (``kress91_fO2_log_for_fe3_fraction``). f = 0 and f = 1 make that
    log ratio diverge, so there is no finite Kress91 root. Seeding both
    of those endpoints with IW plus alkali would give one potential
    (Holzheid IW at 1673.15 K is -9.740754, and a small alkali offset
    does not separate the two inventories) and the forward relation
    would read it as a small ferric fraction. The ledger still writes
    f = 0 as all FeO and f = 1 as all Fe2O3.

    The existing vapour mass-action envelope already bounds melt pO2:
    MELT_DISSOCIATION_PO2_MIN_BAR is 1e-30 bar and
    MELT_DISSOCIATION_PO2_MAX_BAR is 100 bar. f = 0 takes the reducing
    bound and f = 1 the oxidizing bound. That is the dissociation
    envelope, not a Kress91 solution.

    Algebra: log10(fO2/bar) = log10(1e-30) = -30 at f = 0, and
    log10(100) = 2 at f = 1. Interior fractions use the inversion.

    Units: f is dimensionless Fe3+/sum-Fe. T_K is kelvin. pressure_bar
    is bar. The result is dimensionless log10(fO2/bar).

    Sanity: f = 0 returns -30 and f = 1 returns 2. Neither equals
    Holzheid IW at 1673.15 K (-9.740754).
    """

    domain = measured_fe3_fraction_seed_domain(fe3_fraction)
    if domain == MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE:
        bound_bar = (
            MELT_DISSOCIATION_PO2_MIN_BAR
            if float(fe3_fraction) == 0.0
            else MELT_DISSOCIATION_PO2_MAX_BAR
        )
        return math.log10(bound_bar), domain
    return (
        kress91_fO2_log_for_fe3_fraction(
            fe3_fraction=float(fe3_fraction),
            composition_wt_pct=composition_wt_pct,
            T_K=T_K,
            pressure_bar=pressure_bar,
        ),
        domain,
    )


def fe3_fraction_for_prior(
    composition_wt_pct: Mapping[str, float],
    prior: FeRedoxPrior,
) -> float:
    """Ferric fraction used for the one load-time mass split.

    A measured fraction is the cited value. It already matches the seed
    at every temperature, because the seed inverts Kress91 for that
    fraction. A delta_iw prior is the Kress91 split at production IW(T)
    + delta_iw. T is ``KRESS91_FO2_KEY_REFERENCE_T_K`` (1673.15 K),
    inside the 1200-1630 C liquid band. Pressure is
    ``LOAD_FE_SPLIT_PRESSURE_BAR``. Alkali is not added: delta_iw is
    already the offset from production IW.

    The seed stays IW(T) + delta_iw at the adopting temperature, as the
    b-747 potential does. Ledger and seed therefore agree only when the
    adopting temperature is 1673.15 K. The split is not redone on the
    first liquid tick. The atom ledger and the omitted-oxygen credit
    close at load, before any liquid hour, so a later rewrite of FeO
    and Fe2O3 would move mass after that close. A campaign that never
    adopts would also stay all-ferrous for the whole run. 1673.15 K is
    known before that close. ``delta_iw_ledger_seed_gap`` records how
    far the seed-implied fraction at the 1200 C floor sits from this
    frozen fraction.

    Algebra: f = Kress91(fO2 = IW(T_ref) + delta_iw, T_ref, 1 bar) on
    the pre-split wt%. Units: T_ref kelvin, delta_iw dex, f
    dimensionless Fe3+/sum-Fe.

    Sanity: on one composition, f at T_ref equals the forward Kress91
    fraction at IW(T_ref) + delta_iw. The same composition at 1200 C
    differs by the temperature term only.
    """

    if prior.kind == "measured_fe3_fraction":
        return float(prior.value)
    if prior.kind != "delta_iw":
        raise Kress91InvalidControls(
            f"unknown fe redox prior kind {prior.kind!r}"
        )
    mol_fractions = melt_mol_fractions_for_kress91(composition_wt_pct)
    if not mol_fractions or float(mol_fractions.get("FeOt", 0.0)) <= 0.0:
        return 0.0
    fO2_log = feo_iw_log10_fO2_bar(KRESS91_FO2_KEY_REFERENCE_T_K) + float(
        prior.value
    )
    return float(
        kress91_fe3_over_sigma_fe(
            fO2_log=fO2_log,
            mol_fractions=mol_fractions,
            T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
            pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
        )
    )


def delta_iw_ledger_seed_gap(
    composition_wt_pct: Mapping[str, float],
    *,
    delta_iw: float,
    ledger_fe3_fraction: float,
) -> dict[str, float] | None:
    """Size of the frozen ledger fraction against the seed at another T.

    Premise: ``fe3_fraction_for_prior`` freezes a delta_iw split at
    1673.15 K. The seed at temperature T is IW(T) + delta_iw, whose
    Kress91 fraction is not that frozen value. This reports both
    fractions. It does not write a second split.

    Algebra: f_implied(T) = Kress91(fO2 = IW(T) + delta_iw, T, 1 bar)
    on ``composition_wt_pct``. The notice passes the post-split melt,
    so the gap at 1673.15 K is only the composition change from adding
    ferric oxygen. The gap at the 1200 C floor is
    f_implied(1473.15 K) - f_ledger.

    Units: temperatures in K, delta_iw in dex, fractions dimensionless.

    Sanity: SiO2 45, FeO 16.5, Al2O3 10, MgO 10, CaO 10 wt% at
    delta_iw -1.01. The frozen fraction is 0.017789. On that same map
    the split-temperature gap is 0, and the 1200 C gap is 0.001412
    (implied fraction 0.019202). A None return means the composition
    has no FeOt mole fraction to split.
    """

    mol_fractions = melt_mol_fractions_for_kress91(composition_wt_pct)
    if not mol_fractions or float(mol_fractions.get("FeOt", 0.0)) <= 0.0:
        return None
    offset = float(delta_iw)
    ledger = float(ledger_fe3_fraction)
    floor_T_K = KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15

    def _implied(temperature_K: float) -> float:
        return float(
            kress91_fe3_over_sigma_fe(
                fO2_log=feo_iw_log10_fO2_bar(temperature_K) + offset,
                mol_fractions=mol_fractions,
                T_K=temperature_K,
                pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
            )
        )

    at_split = _implied(KRESS91_FO2_KEY_REFERENCE_T_K)
    at_floor = _implied(floor_T_K)
    return {
        "split_temperature_K": KRESS91_FO2_KEY_REFERENCE_T_K,
        "implied_fe3_fraction_at_split_T": at_split,
        "fe3_fraction_gap_at_split_T": at_split - ledger,
        "calibration_floor_temperature_K": floor_T_K,
        "implied_fe3_fraction_at_calibration_floor": at_floor,
        "fe3_fraction_gap_at_calibration_floor": at_floor - ledger,
    }


def omitted_ferric_oxygen_kg(feot_kg: float, fe3_fraction: float) -> float:
    """Oxygen mass a total-Fe-as-FeO analysis omits when fraction f is Fe3+.

    Premise: FeOT counts every Fe atom as FeO. Moving fraction f of those
    atoms into Fe2O3 adds half an oxygen atom per ferric Fe
    (2 FeO + O -> Fe2O3) and does not change the Fe-atom count.

    Algebra: n_Fe = m_FeOT / M_FeO
             delta_m = f * n_Fe * 0.5 * M_O
                     = f * m_FeOT * 0.5 * OXYGEN_IN_FEO
    OXYGEN_IN_FEO is 15.999/71.844, so 0.5 * OXYGEN_IN_FEO is 0.111345415 kg
    oxygen per kg FeOT at f = 1.

    The dispatch text's 8.0e-3 * f * FeOT wt% * batch_kg is 0.800 kg oxygen
    per kg FeOT at f = 1, about 7.2 times this molar result, and is not used.

    Units: m_FeOT and the result share a mass unit (kg on the ledger, wt%
    when the caller passes a wt% FeOT). f is dimensionless Fe3+/sum-Fe.

    Sanity: f = 1 and m_FeOT = 100 kg. delta_m = 100 * 0.111345415 =
    11.1345415 kg.
    """

    fraction = float(fe3_fraction)
    mass = float(feot_kg)
    if not math.isfinite(fraction) or not math.isfinite(mass):
        raise Kress91InvalidControls("ferric oxygen inputs must be finite")
    if fraction < 0.0 or fraction > 1.0:
        raise Kress91InvalidControls("ferric fraction must be between 0 and 1")
    if mass <= 0.0 or fraction == 0.0:
        return 0.0
    return fraction * mass * 0.5 * OXYGEN_IN_FEO


def feo_fe2o3_kg_from_feot(
    feot_kg: float, fe3_fraction: float
) -> tuple[float, float, float]:
    """Return FeO mass, Fe2O3 mass, and the omitted oxygen. Fe atoms stay put.

    n_Fe2 = (1 - f) * n_Fe and n_Fe3 = f * n_Fe.
    m_FeO = n_Fe2 * M_FeO = (1 - f) * m_FeOT.
    m_Fe2O3 = n_Fe3 * M_Fe2O3 / 2 = f * m_FeOT / FEOT_FROM_FE2O3,
    because FEOT_FROM_FE2O3 = 2 M_FeO / M_Fe2O3.
    M_Fe2O3 = 2 M_FeO + M_O, so m_FeO + m_Fe2O3 = m_FeOT + omitted oxygen.
    """

    oxygen_kg = omitted_ferric_oxygen_kg(feot_kg, fe3_fraction)
    fraction = float(fe3_fraction)
    ferrous_kg = (1.0 - fraction) * float(feot_kg)
    if fraction <= 0.0 or float(feot_kg) <= 0.0:
        return float(feot_kg), 0.0, 0.0
    ferric_kg = fraction * float(feot_kg) / FEOT_FROM_FE2O3
    return ferrous_kg, ferric_kg, oxygen_kg


def apply_feot_split_to_oxide_kg(
    oxide_kg: dict[str, float], fe3_fraction: float
) -> float:
    """Rewrite FeO and Fe2O3 in ``oxide_kg``. Return the added oxygen mass."""

    feo_kg = float(oxide_kg.get("FeO", 0.0) or 0.0)
    fe2o3_kg = float(oxide_kg.get("Fe2O3", 0.0) or 0.0)
    feot_kg = feo_kg + fe2o3_kg * FEOT_FROM_FE2O3
    ferrous_kg, ferric_kg, oxygen_kg = feo_fe2o3_kg_from_feot(
        feot_kg, fe3_fraction
    )
    if feot_kg <= 0.0 or float(fe3_fraction) <= 0.0:
        return 0.0
    if ferrous_kg > 1.0e-15:
        oxide_kg["FeO"] = ferrous_kg
    else:
        oxide_kg.pop("FeO", None)
    if ferric_kg > 1.0e-15:
        oxide_kg["Fe2O3"] = ferric_kg
    else:
        oxide_kg.pop("Fe2O3", None)
    return oxygen_kg


@dataclass(frozen=True)
class LoadFeRedox:
    """The one load-time iron resolution every consumer reads.

    ``authority`` is ``measured``, ``prior``, ``lower_bound``, or None when
    the melt has no oxide iron and no prior. ``fe3_fraction`` is the fraction
    written into the ledger, or None when the ledger stays all-ferrous.
    """

    authority: str | None
    prior: FeRedoxPrior | None
    fe3_fraction: float | None
    added_oxygen_kg: float


def _oxide_wt_pct_from_kg(oxide_kg: Mapping[str, float]) -> dict[str, float]:
    total_kg = sum(float(kg) for kg in oxide_kg.values() if float(kg) > 0.0)
    if total_kg <= 0.0:
        return {}
    return {
        species: float(kg) / total_kg * 100.0
        for species, kg in oxide_kg.items()
        if float(kg) > 0.0
    }


def resolve_load_fe_redox(
    oxide_kg: dict[str, float],
    prior: FeRedoxPrior | None,
) -> LoadFeRedox:
    """Split ``oxide_kg`` once from ``prior``. No prior leaves the ledger ferrous.

    The ferric fraction for a delta_iw prior is evaluated on the pre-split
    wt% of this same map, at ``KRESS91_FO2_KEY_REFERENCE_T_K`` and
    ``LOAD_FE_SPLIT_PRESSURE_BAR``. Added oxygen stays in Fe2O3; the caller
    credits that mass on the stage-0 external-input ledger so the batch
    balance still closes. A map that already carries Fe2O3 and has no prior
    is a declared pair: it is left unchanged and is not labelled a lower bound.
    """

    feo_kg = float(oxide_kg.get("FeO", 0.0) or 0.0)
    fe2o3_kg = float(oxide_kg.get("Fe2O3", 0.0) or 0.0)
    feot_kg = feo_kg + fe2o3_kg * FEOT_FROM_FE2O3
    if prior is None:
        if feot_kg <= 0.0:
            return LoadFeRedox(None, None, None, 0.0)
        # A declared Fe2O3 mass is already a split. It is not the unresolved
        # total-Fe lower bound, and it is not rewritten.
        if fe2o3_kg > 0.0:
            return LoadFeRedox(None, None, None, 0.0)
        return LoadFeRedox("lower_bound", None, None, 0.0)
    authority = (
        "measured" if prior.kind == "measured_fe3_fraction" else "prior"
    )
    if feot_kg <= 0.0:
        return LoadFeRedox(authority, prior, None, 0.0)
    composition_wt_pct = _oxide_wt_pct_from_kg(oxide_kg)
    fraction = fe3_fraction_for_prior(composition_wt_pct, prior)
    added_oxygen_kg = apply_feot_split_to_oxide_kg(oxide_kg, fraction)
    return LoadFeRedox(authority, prior, fraction, added_oxygen_kg)


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
