"""Shared composition conversions used by scoring and condition resolution.

Element mass fraction with measured oxygen (t-1609) lives here, next to the
oxide wt% conversion. Migration and scoring call these functions. They do
not keep a second copy of the excess algebra.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.battery.enums import (
    ELEMENT_WT_PERCENT_BASIS,
    NoticeKind,
    RefusalReason,
)
from simulator.battery.records import Composition
from simulator.reference_data.janaf import formula_composition

# Spaces, underscores, hyphens, and dashes are not part of the scale token.
_VOLUME_BASIS_DROPS = str.maketrans("", "", " _-\u2013\u2014")
_VOLUME_PERCENT_SCALES = frozenset({"volpercent", "volumepercent"})
_VOLUME_PPM_SCALES = frozenset({"volppm", "volumeppm", "vppm"})


# Absolute tolerance on a phase point's mole-fraction sum. 1 ± 0.02.
# Babaian's unclosed liquid (sum 0.913) is outside this band.
_MOLE_FRACTION_CLOSURE = Decimal("0.02")


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None means unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


def ideal_gas_volume_scale(token: object) -> str | None:
    """Classify a printed volume basis as ``percent`` or ``ppm``.

    Bare ``ppm`` is not a volume scale. It can be a mass-ppm amount.
    """

    if not isinstance(token, str):
        return None
    folded = (
        token.strip().casefold().replace("%", "percent").translate(_VOLUME_BASIS_DROPS)
    )
    if folded in _VOLUME_PERCENT_SCALES:
        return "percent"
    if folded in _VOLUME_PPM_SCALES:
        return "ppm"
    return None


def ideal_gas_volume_amount_to_mole_fraction(
    amount: Decimal, printed_basis: str
) -> Decimal:
    """Convert a printed ideal-gas volume amount to a mole fraction.

    Premise: Amagat's law. For an ideal gas the partial volume equals the
    partial pressure, so V_i/V = n_i/n. Flue gas at about 1 bar and
    1100-1600 K has Z-1 < 1e-3, below the printed composition precision.
    Algebra: x_i = (vol%)/100 = (vol-ppm) * 1e-6. The ppm dopants are not
    folded back into the percent closure.
    Unit check: vol% and vol-ppm are both volume/volume, so the quotient is
    dimensionless, the same dimension as a mole fraction.
    Sanity: 15.2 vol% -> 0.152; 20 vol-ppm -> 2e-5.
    """

    scale = ideal_gas_volume_scale(printed_basis)
    if scale == "percent":
        return amount / Decimal("100")
    if scale == "ppm":
        return amount * Decimal("1e-6")
    raise ValueError(f"unrecognised ideal-gas volume basis {printed_basis!r}")


def composition_wt_pct(composition: Composition) -> dict[str, float] | None:
    """Convert an engine-basis composition to oxide wt%.

    Premise: Identity.composition is a complete ordered map on
    mol_inventory or mole_fraction. Equilibrate cells take oxide wt%.
    Algebra: m_i = n_i M_i; w_i = 100 m_i / Σ m_j.
    Unit check: (mol)×(g/mol) = g; g/g × 100 = wt%.
    Sanity: equal moles of SiO2 (60.084) and MgO (40.304) → ~59.9/40.1 wt%.
    """

    from simulator.diagnostic_helpers.binary_pot_scoring import oxide_molar_mass_g_mol

    masses: dict[str, float] = {}
    for oxide, amount in composition.components:
        if parse_species_formula(oxide) is None:
            return None
        n = float(amount)
        if not math.isfinite(n) or n < 0.0:
            return None
        mass = n * oxide_molar_mass_g_mol(oxide)
        if not math.isfinite(mass) or mass < 0.0:
            return None
        masses[oxide] = mass
    total = sum(masses.values())
    if total <= 0.0:
        return None
    return {key: 100.0 * value / total for key, value in masses.items() if value > 0.0}


def mass_weighted_mixture_wt_pct(
    parts: Sequence[tuple[Mapping[str, Decimal], Decimal]],
) -> dict[str, Decimal]:
    """Mass-weighted sum of printed wt% vectors. Does not renormalize.

    Premise: each part is (component wt% map w_k, mass fraction f_k) of one
    mixture. The fractions sum to 1. A named material contributes its printed
    oxide wt% scaled by f_material. A metallic element is its own component
    at 100 wt% of f_element (Mg0). It is never folded into its oxide: the
    metal is the reductant charge, and the oxide already in the material
    stays that oxide.
    Algebra: w_mix,j = sum_k f_k * w_k,j.
    Unit check: f is dimensionless; w is wt% (g per 100 g of that part);
    the product is wt% of the mixture.
    Sanity: 50 wt% pure Mg plus 50 wt% of a material that is 50 SiO2 / 50 MgO
    gives w_Mg = 50, w_SiO2 = 25, w_MgO = 25. MgO is not 75.
    """

    mixed: dict[str, Decimal] = {}
    for weights, fraction in parts:
        for name, weight in weights.items():
            contribution = fraction * weight
            if contribution == 0:
                continue
            mixed[name] = mixed.get(name, Decimal("0")) + contribution
    return mixed


def mole_percent_to_mole_fraction(percent: Decimal) -> Decimal:
    """x = mol% / 100.

    Premise: the printed phase scalar is mole percent on a 0–100 scale,
    not a mole fraction. This is not ``migrate._composition_from_plain``:
    that converts a bulk composition map and requires the map to sum to
    100. A phase row is one number; closure is the sibling sum in
    ``phase_mole_fractions_to_component_wt_percent``.

    Algebra: x = percent / 100.
    Unit check: mol% / 100 = mol/mol.
    Worked example: 50 mol% → 0.50; 66.8 mol% → 0.668.
    """

    return percent / Decimal(100)


def phase_mole_fractions_to_component_wt_percent(
    component: str,
    mole_fractions: Mapping[str, Decimal],
) -> tuple[Decimal | None, str | None]:
    """Weight percent of one oxide, or a typed refusal. Does not renormalise.

    Premise: each value is an oxide mole fraction of one phase point, and
    the siblings are the whole phase. The engine map is wt% of an
    engine-map oxide key. An unclosed set (sum outside 1 ± 0.02) stays
    refused: dividing by the partial sum would invent the missing fraction.

    Algebra: w_i = 100 * x_i * M_i / sum_j(x_j * M_j). M comes from
    ``oxide_molar_mass`` (the project CIAAW table), not a rounded mass.

    Unit check: (mol/mol) * (g/mol) = g per mole of phase; g/g * 100 = wt%.

    Worked example with the ruling's rounded masses SiO2 = 60.08 and
    CaO = 56.08: w(SiO2) = 100 * 0.5 * 60.08 / (0.5 * 60.08 + 0.5 * 56.08)
    = 51.72 wt%. The CIAAW masses this function uses are SiO2 = 60.083 and
    CaO = 56.077, so the same equal-mole point is 51.724346 wt%.
    """

    from simulator.battery.migrate import is_engine_map_oxide_key, oxide_molar_mass

    if component not in mole_fractions:
        return None, "phase_mole_fraction_component_absent"
    if not is_engine_map_oxide_key(component):
        return None, "phase_component_not_engine_map_oxide"
    total = Decimal(0)
    weighted: dict[str, Decimal] = {}
    for name, amount in mole_fractions.items():
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            return None, "phase_mole_fraction_non_finite"
        if not is_engine_map_oxide_key(name):
            return None, (
                "phase_component_not_engine_map_oxide"
                if name == component
                else "phase_sibling_not_engine_map_oxide"
            )
        weighted[name] = amount * oxide_molar_mass(name)
        total += amount
    # Closure is on the mole fractions. The mass sum is not a renormaliser.
    if abs(total - Decimal(1)) > _MOLE_FRACTION_CLOSURE:
        return None, "phase_mole_fraction_set_unclosed"
    denominator = sum(weighted.values(), Decimal(0))
    if denominator <= 0:
        return None, "phase_mole_fraction_set_unclosed"
    return Decimal(100) * weighted[component] / denominator, None


def phase_printed_mole_amounts_to_wt_percent(
    component: str,
    printed: Mapping[str, Decimal],
    *,
    mole_percent: bool,
) -> tuple[Decimal | None, str | None]:
    """Prose mole percent is x = mol%/100, then the closed mole-fraction rule.

    Premise: a prose mole-percent sibling prints 0–100. A mole_fraction
    token is already x. Mixing the two scales is the caller's grouping
    error; this function applies one scale to the whole map.

    Worked example: 50 mol% SiO2 and 50 mol% CaO are the equal-mole point
    in ``phase_mole_fractions_to_component_wt_percent``.
    """

    if not mole_percent:
        return phase_mole_fractions_to_component_wt_percent(component, printed)
    fractions: dict[str, Decimal] = {}
    for name, amount in printed.items():
        if not isinstance(amount, Decimal) or not amount.is_finite():
            return None, "phase_mole_fraction_non_finite"
        fractions[name] = mole_percent_to_mole_fraction(amount)
    return phase_mole_fractions_to_component_wt_percent(component, fractions)


def phase_ppm_mass_as_wt_percent(
    species: str, ppm: Decimal
) -> tuple[Decimal | None, str | None]:
    """wt% = ppm / 1e4, only when ``species`` is the engine-map oxide key.

    Premise: ppm_mass counts mass parts of the named species per 1e6.
    The engine phase map is oxide weight percent. An element (Ni, Mo) or
    any other non-key is a different thing and stays refused.

    Algebra: ``enums.ppm_mass_to_wt_percent`` is the only divisor
    (wt% = ppm / 1e4). This function does not write a second one.

    Unit check: (mass fraction * 1e6) / 1e4 = mass fraction * 100 = wt%.
    Worked example: 10000 ppm of an oxide key = 1 wt%; 2500 ppm = 0.25 wt%.
    """

    from simulator.battery.enums import ppm_mass_to_wt_percent
    from simulator.battery.migrate import is_engine_map_oxide_key

    if not is_engine_map_oxide_key(species):
        return None, "element_ppm_not_oxide_wt_percent"
    if not isinstance(ppm, Decimal) or not ppm.is_finite():
        return None, "phase_ppm_mass_non_finite"
    return ppm_mass_to_wt_percent(ppm), None


# Printed elemental wt% with measured O. Identity stores the cation relabel
# (all Fe as FeO) under this basis string; it is not itself a phase token.
ELEMENT_WT_PERCENT_FEO_T_BASIS = "element_wt_percent_feo_t"

# Closed observables whose engine redox input is fO2 / the conditions.
# The Fe3+ derived from that same row's oxygen is not an engine input.
CIRCULAR_MEASURED_OXYGEN_OBSERVABLES = frozenset(
    {
        "oxygen_excess",
        "fe3_fe2_ratio",
        "fe3_over_sum_fe",
        "valence_ratio",
    }
)

_OXIDE_FEO = "FeO"
_OXIDE_FE2O3 = "Fe2O3"
_IRON_ALIASES = {"FeT": "Fe", "Fe_T": "Fe"}
_LISTED_REDOX_ELEMENTS = ("Ti", "Mn", "Cr")


@dataclass(frozen=True)
class _CationOxide:
    element: str
    oxide: str
    cations_per_formula: int
    valence: int


# Reference valences from t-1609. Oxygen atoms per cation = valence / 2.
# Mn2+ and Cr3+ are the stated extras. No other valence is assigned here.
_CATION_OXIDES = (
    _CationOxide("Si", "SiO2", 1, 4),
    _CationOxide("Ti", "TiO2", 1, 4),
    _CationOxide("Al", "Al2O3", 2, 3),
    _CationOxide("Fe", _OXIDE_FEO, 1, 2),
    _CationOxide("Mg", "MgO", 1, 2),
    _CationOxide("Ca", "CaO", 1, 2),
    _CationOxide("Na", "Na2O", 2, 1),
    _CationOxide("K", "K2O", 2, 1),
    _CationOxide("Mn", "MnO", 1, 2),
    _CationOxide("Cr", "Cr2O3", 2, 3),
)
_CATION_BY_ELEMENT = {row.element: row for row in _CATION_OXIDES}


@dataclass(frozen=True)
class ElementWtNotice:
    kind: NoticeKind
    reason: str
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class ElementMassFractionRedox:
    """Printed elements, the FeO_T relabel, and the engine redox input.

    ``redox_kind`` is ``ferric_fraction`` (pass the FeO/Fe2O3 split; do not
    pass fO2), ``conditions`` (pass fO2; do not pass the derived fraction),
    ``stoichiometry_only`` (no iron and the oxygen matches the reference
    valences), or ``refusal``. ``refusal`` is a token, not a clipped number.
    """

    printed_wt_percent: tuple[tuple[str, Decimal], ...]
    oxide_mole_fractions: tuple[tuple[str, Decimal], ...] | None
    oxygen_excess_mol_per_100g: Decimal | None
    oxygen_excess_sigma_mol_per_100g: Decimal | None
    oxygen_uncertainty_not_printed: bool
    fe3_over_sum_fe: Decimal | None
    ferric_split_wt_percent: tuple[tuple[str, Decimal], ...] | None
    refusal: str | None
    redox_kind: str
    notices: tuple[ElementWtNotice, ...]


def canonical_element_symbol(name: str) -> str:
    """Map a printed total-iron label onto Fe. Other symbols stay as printed."""

    token = str(name).strip()
    return _IRON_ALIASES.get(token, token)


def chloride_component_symbol(name: str) -> str | None:
    """A bulk component whose non-oxygen elements are exactly {Cl}.

    Cl and Cl2 qualify. NaCl does not: adopting it would change the sodium
    inventory. None means this name is not a chloride component.
    """

    parsed = parse_species_formula(str(name).strip())
    if not parsed:
        return None
    elements = {element for element, count in parsed if count}
    if elements - {"O"} == {"Cl"}:
        return str(name).strip()
    return None


def parse_printed_sigma(value: object) -> Decimal | None:
    """A finite nonnegative printed uncertainty. Anything else is not a band."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        if "sigma" in value:
            return parse_printed_sigma(value.get("sigma"))
        return None
    try:
        sigma = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not sigma.is_finite() or sigma < 0:
        return None
    return sigma


def printed_oxygen_sigma_from_row(
    values: Mapping[str, object],
) -> tuple[Decimal | None, str | None]:
    """Printed O wt% sigma from the t-1609 row fields.

    ``oxygen_wt_percent_sigma`` wins. ``element_wt_percent_uncertainty.O``
    (a decimal or a ``sigma`` field) is the fallback. When both are printed
    and differ, the explicit field is kept and the reason names the clash.
    """

    explicit = parse_printed_sigma(values.get("oxygen_wt_percent_sigma"))
    nested_raw = values.get("element_wt_percent_uncertainty")
    nested_o = None
    if isinstance(nested_raw, Mapping):
        nested_o = nested_raw.get("O")
    nested = parse_printed_sigma(nested_o)
    if explicit is not None and nested is not None and explicit != nested:
        return explicit, "oxygen_wt_percent_sigma_disagrees_with_element_uncertainty"
    if explicit is not None:
        return explicit, None
    return nested, None


def element_wt_percent_map_from_row(
    values: Mapping[str, object],
) -> Mapping[str, object] | None:
    """The printed element wt% map. A located-state wrapper is not this map."""

    raw = values.get(ELEMENT_WT_PERCENT_BASIS)
    if isinstance(raw, Mapping) and "state" not in raw and "components" not in raw:
        return raw
    return None


def printed_component_identified(
    name: str, *, known_components: Sequence[str] = ()
) -> bool:
    """True when the token is a known component or a closed formula with weights.

    ``Na2O_plus_K2O`` is not a formula. ``XxO`` parses as a pattern but Xx has
    no atomic weight, so it is not identified. H2O, Cl, Ag2O, I2O, and Re are.
    """

    token = str(name).strip()
    if token in set(known_components):
        return True
    parsed = parse_species_formula(token)
    if not parsed:
        return False
    return all(
        element in ATOMIC_WEIGHTS_G_PER_MOL
        for element, count in parsed
        if count
    )


def unidentified_component_refusal(names: Sequence[str]) -> str:
    """The typed refusal for a printed component the parser cannot identify."""

    return (
        "printed oxide wt% map has unsupported numeric component(s): "
        + ", ".join(sorted(str(name) for name in names))
    )


def partition_engine_components(
    components: Mapping[str, object],
    *,
    engine_components: Sequence[str],
    retain_components: Sequence[str] = (),
) -> tuple[tuple[tuple[str, Decimal], ...], tuple[tuple[str, Decimal], ...]]:
    """Split a printed component map into the engine subset and the exclusions.

    A name passes when it is in ``engine_components`` or in
    ``retain_components`` (components the oxide converter already stored).
    H2O and Cl pass only when that engine lists them. Every other component
    is returned with its input amount, including a printed zero. This
    function does not rescale and does not drop a name quietly.
    """

    engine = {str(name) for name in engine_components}
    retain = {str(name) for name in retain_components}
    kept: list[tuple[str, Decimal]] = []
    excluded: list[tuple[str, Decimal]] = []
    for raw_name, raw_amount in components.items():
        name = str(raw_name).strip()
        amount = _as_printed_amount(raw_amount)
        if amount is None or amount < 0:
            continue
        if name in engine or name in retain:
            kept.append((name, amount))
        else:
            excluded.append((name, amount))
    return tuple(kept), tuple(excluded)


def excluded_component_notice_reason(
    excluded: Sequence[tuple[str, Decimal]], *, unit: str
) -> str:
    """Name each excluded component and the amount that was not passed on."""

    named = ", ".join(
        f"{name} {_amount_token(amount)} {unit}" for name, amount in excluded
    )
    return f"excluded_unsupported_composition_components: {named}"


def _amount_token(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _atomic_weight(symbol: str) -> Decimal:
    return Decimal(str(ATOMIC_WEIGHTS_G_PER_MOL[symbol]))


def _formula_molar_mass(formula: str) -> Decimal:
    parsed = parse_species_formula(formula)
    if not parsed:
        raise KeyError(formula)
    total = Decimal(0)
    for element, count in parsed:
        total += _atomic_weight(element) * Decimal(str(count))
    return total


def _is_circular_observable(observable: object | None) -> bool:
    if observable is None:
        return False
    token = getattr(observable, "value", observable)
    return str(token) in CIRCULAR_MEASURED_OXYGEN_OBSERVABLES


def _as_printed_amount(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def element_mass_fraction_redox(
    elements: Mapping[str, object],
    *,
    oxygen_wt_percent_sigma: Decimal | None = None,
    observable: object | None = None,
    chloride_components: Sequence[str] = (),
) -> ElementMassFractionRedox:
    """Element wt% with measured O, the FeO_T relabel, and the redox input.

    Premise (t-1609): the printed analysis gives elemental mass fractions,
    and O is measured. Rebuilding O from cation stoichiometry would erase
    the excess oxygen the measurement exists to report. The cation part is
    relabelled onto the oxide basis with every Fe atom counted as FeO
    (FeO_T). That relabel does not use the measured O. Engines consume the
    relabel plus a separate redox input.

    Algebra, per 100 g (a printed wt% number is already g per 100 g):
        n_i = w_i / M_i
        oxygen demand = sum_i n_i * (z_i / 2)
        excess = n_O,meas - oxygen demand
    Reference valences: Si4+ Ti4+ Al3+ Fe2+ Mg2+ Ca2+ Na+ K+, and Mn2+ Cr3+
    when those elements are printed. O, Pt, and Cl are not in the sum.
    When 0 <= excess <= n_Fe/2 and n_Fe > 0:
        Fe3+/sum Fe = 2 * excess / n_Fe
    Otherwise the excess is outside the oxide component space.

    Unit check: (g/100 g) / (g/mol) = mol/100 g. z_i/2 is O atoms per
    cation, so the demand and the excess are mol O per 100 g.
    sigma(excess) = sigma(w_O) / M_O when a printed O uncertainty exists.

    Scale check (ruling): Fe at 10 wt% is 0.179 mol/100 g (10/55.845).
    Full oxidation of that iron needs 0.0895 mol O. A 1 wt% O error is
    0.0625 mol (1/15.999), which is ±0.7 in Fe3+/sum Fe
    (2 * 0.0625 / 0.179). The derived redox is only as good as the O
    analysis, so a missing O uncertainty is carried without a band.
    """

    notices: list[ElementWtNotice] = []
    printed: list[tuple[str, Decimal]] = []
    canonical: dict[str, Decimal] = {}
    duplicate_iron = False
    bad_amount: str | None = None
    duplicate_symbol: str | None = None
    for raw_name, raw_amount in elements.items():
        name = str(raw_name).strip()
        amount = _as_printed_amount(raw_amount)
        if amount is None or amount < 0:
            bad_amount = name
            break
        printed.append((name, amount))
        symbol = canonical_element_symbol(name)
        if symbol in canonical:
            if symbol == "Fe":
                duplicate_iron = True
            else:
                duplicate_symbol = symbol
                break
            continue
        canonical[symbol] = amount

    if bad_amount is not None:
        return ElementMassFractionRedox(
            printed_wt_percent=tuple(printed),
            oxide_mole_fractions=None,
            oxygen_excess_mol_per_100g=None,
            oxygen_excess_sigma_mol_per_100g=None,
            oxygen_uncertainty_not_printed=True,
            fe3_over_sum_fe=None,
            ferric_split_wt_percent=None,
            refusal=f"element_amount_not_a_nonnegative_number: {bad_amount}",
            redox_kind="refusal",
            notices=(),
        )
    if duplicate_symbol is not None:
        return ElementMassFractionRedox(
            printed_wt_percent=tuple(printed),
            oxide_mole_fractions=None,
            oxygen_excess_mol_per_100g=None,
            oxygen_excess_sigma_mol_per_100g=None,
            oxygen_uncertainty_not_printed=True,
            fe3_over_sum_fe=None,
            ferric_split_wt_percent=None,
            refusal=f"duplicate_element: {duplicate_symbol}",
            redox_kind="refusal",
            notices=(),
        )
    if duplicate_iron:
        return ElementMassFractionRedox(
            printed_wt_percent=tuple(printed),
            oxide_mole_fractions=None,
            oxygen_excess_mol_per_100g=None,
            oxygen_excess_sigma_mol_per_100g=None,
            oxygen_uncertainty_not_printed=True,
            fe3_over_sum_fe=None,
            ferric_split_wt_percent=None,
            refusal="duplicate_total_iron",
            redox_kind="refusal",
            notices=(),
        )

    unlisted = [
        symbol
        for symbol, amount in canonical.items()
        if amount > 0
        and symbol not in _CATION_BY_ELEMENT
        and symbol not in {"O", "Pt", "Cl"}
    ]
    if unlisted:
        names = ", ".join(unlisted)
        return ElementMassFractionRedox(
            printed_wt_percent=tuple(printed),
            oxide_mole_fractions=None,
            oxygen_excess_mol_per_100g=None,
            oxygen_excess_sigma_mol_per_100g=None,
            oxygen_uncertainty_not_printed=True,
            fe3_over_sum_fe=None,
            ferric_split_wt_percent=None,
            refusal=f"element_reference_valence_not_stated: {names}",
            redox_kind="refusal",
            notices=(),
        )

    if "Pt" in canonical:
        notices.append(
            ElementWtNotice(
                NoticeKind.INPUT_OMITTED,
                "pt_excluded_capsule_contamination",
                ("Pt",),
            )
        )

    chloride_name = _one_chloride_component(chloride_components)
    chlorine = canonical.get("Cl")
    include_chlorine = chlorine is not None and chlorine > 0 and chloride_name is not None
    if chlorine is not None and not include_chlorine:
        reason = (
            "cl_excluded_ambiguous_engine_chloride_components"
            if _chloride_names(chloride_components)[1]
            else "cl_excluded_no_engine_chloride_component"
        )
        notices.append(ElementWtNotice(NoticeKind.INPUT_OMITTED, reason, ("Cl",)))

    formula_moles: dict[str, Decimal] = {}
    oxygen_demand = Decimal(0)
    n_fe = Decimal(0)
    for row in _CATION_OXIDES:
        weight = canonical.get(row.element)
        if weight is None or weight == 0:
            continue
        cation_moles = weight / _atomic_weight(row.element)
        formula_moles[row.oxide] = cation_moles / Decimal(row.cations_per_formula)
        oxygen_demand += cation_moles * Decimal(row.valence) / Decimal(2)  # t-1609: z_i / 2
        if row.element == "Fe":
            n_fe = cation_moles

    if include_chlorine and chloride_name is not None and chlorine is not None:
        parsed = parse_species_formula(chloride_name)
        cl_atoms = Decimal(0)
        if parsed:
            cl_atoms = sum(
                (Decimal(str(count)) for element, count in parsed if element == "Cl"),
                Decimal(0),
            )
        if cl_atoms > 0:
            formula_moles[chloride_name] = (chlorine / _atomic_weight("Cl")) / cl_atoms

    if not formula_moles:
        return ElementMassFractionRedox(
            printed_wt_percent=tuple(printed),
            oxide_mole_fractions=None,
            oxygen_excess_mol_per_100g=None,
            oxygen_excess_sigma_mol_per_100g=None,
            oxygen_uncertainty_not_printed=True,
            fe3_over_sum_fe=None,
            ferric_split_wt_percent=None,
            refusal="element_wt_percent_has_no_cation",
            redox_kind="refusal",
            notices=tuple(notices),
        )

    mole_total = sum(formula_moles.values(), Decimal(0))
    oxide_moles = tuple(
        (name, amount / mole_total) for name, amount in formula_moles.items()
    )

    measured_oxygen = "O" in canonical
    excess: Decimal | None = None
    sigma_excess: Decimal | None = None
    uncertainty_missing = True
    if measured_oxygen:
        excess = canonical["O"] / _atomic_weight("O") - oxygen_demand
        if oxygen_wt_percent_sigma is not None:
            uncertainty_missing = False
            sigma_excess = oxygen_wt_percent_sigma / _atomic_weight("O")
        else:
            notices.append(
                ElementWtNotice(
                    NoticeKind.INPUT_OMITTED,
                    "oxygen_uncertainty_not_printed",
                )
            )
    else:
        notices.append(
            ElementWtNotice(NoticeKind.INPUT_OMITTED, "measured_oxygen_not_printed")
        )

    present_redox = tuple(
        element
        for element in _LISTED_REDOX_ELEMENTS
        if canonical.get(element, Decimal(0)) > 0
    )
    fe3: Decimal | None = None
    refusal: str | None = None
    redox_kind = "refusal"
    split: tuple[tuple[str, Decimal], ...] | None = None
    if not measured_oxygen:
        refusal = "measured_oxygen_not_printed"
    elif excess is None:
        refusal = "measured_oxygen_not_printed"
    elif n_fe == 0 and excess == 0:
        redox_kind = "stoichiometry_only"
    elif excess < 0 or n_fe == 0 or excess > n_fe / Decimal(2):
        refusal = RefusalReason.OXYGEN_EXCESS_OUTSIDE_OXIDE_COMPONENT_SPACE.value
        notices.append(
            ElementWtNotice(
                NoticeKind.ENGINE_MODEL_CAVEAT,
                RefusalReason.OXYGEN_EXCESS_OUTSIDE_OXIDE_COMPONENT_SPACE.value,
            )
        )
    else:
        fe3 = (Decimal(2) * excess) / n_fe
        redox_kind = "ferric_fraction"
        listed = ", ".join(present_redox)
        reason = NoticeKind.REDOX_FROM_MEASURED_OXYGEN_FE_ONLY_ASSUMPTION.value
        if listed:
            reason = f"{reason}: {listed}"
        notices.append(
            ElementWtNotice(
                NoticeKind.REDOX_FROM_MEASURED_OXYGEN_FE_ONLY_ASSUMPTION,
                reason,
            )
        )
        split = _ferric_split_wt_percent(formula_moles, n_fe, fe3)

    if _is_circular_observable(observable) and oxide_moles:
        redox_kind = "conditions"
        split = None

    return ElementMassFractionRedox(
        printed_wt_percent=tuple(printed),
        oxide_mole_fractions=oxide_moles,
        oxygen_excess_mol_per_100g=excess,
        oxygen_excess_sigma_mol_per_100g=sigma_excess,
        oxygen_uncertainty_not_printed=uncertainty_missing,
        fe3_over_sum_fe=fe3,
        ferric_split_wt_percent=split,
        refusal=refusal,
        redox_kind=redox_kind,
        notices=tuple(notices),
    )


def _chloride_names(names: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    pure = tuple(
        name for name in names if chloride_component_symbol(str(name)) is not None
    )
    return pure, len(pure) > 1


def _one_chloride_component(names: Sequence[str]) -> str | None:
    pure, ambiguous = _chloride_names(names)
    if ambiguous or len(pure) != 1:
        return None
    return pure[0]


def _ferric_split_wt_percent(
    formula_moles: Mapping[str, Decimal],
    n_fe: Decimal,
    fe3_over_sum_fe: Decimal,
) -> tuple[tuple[str, Decimal], ...]:
    """FeO_T plus Fe3+/sum Fe, as oxide wt% at the engine-call boundary.

    n_Fe3+ = r * n_Fe, n_Fe2O3 = n_Fe3+ / 2, n_FeO = n_Fe - n_Fe3+.
    Exact zeros are omitted. This is not stored on the identity.
    """

    n_fe3 = fe3_over_sum_fe * n_fe
    n_feo = n_fe - n_fe3
    n_fe2o3 = n_fe3 / Decimal(2)
    masses: list[tuple[str, Decimal]] = []
    for name, moles in formula_moles.items():
        if name == _OXIDE_FEO:
            continue
        masses.append((name, moles * _formula_molar_mass(name)))
    if n_feo > 0:
        masses.append((_OXIDE_FEO, n_feo * _formula_molar_mass(_OXIDE_FEO)))
    if n_fe2o3 > 0:
        masses.append((_OXIDE_FE2O3, n_fe2o3 * _formula_molar_mass(_OXIDE_FE2O3)))
    total = sum((mass for _name, mass in masses), Decimal(0))
    return tuple((name, Decimal(100) * mass / total) for name, mass in masses)


def element_wt_percent_of_oxide_phase(
    oxide_wt_percent: Mapping[str, object],
    element: str,
) -> Decimal | None:
    """Element wt% of an oxide-normalised phase.

    Cation mass is the oxide wt% times the cation share of that oxide's
    molar mass. Iron sums FeO and Fe2O3. Oxygen sums the oxygen share of
    every parsed oxide. The denominator is the oxide phase, not the printed
    100 g analysis (Pt, Cl, and any unassigned excess are outside it).
    """

    symbol = canonical_element_symbol(element)
    parsed_rows: list[tuple[str, Decimal, tuple[tuple[str, float], ...]]] = []
    for name, raw in oxide_wt_percent.items():
        amount = _as_printed_amount(raw)
        if amount is None or amount < 0:
            return None
        parsed = parse_species_formula(str(name))
        if not parsed or amount == 0:
            continue
        parsed_rows.append((str(name), amount, parsed))
    if symbol == "O":
        total = Decimal(0)
        weight = _atomic_weight("O")
        for _name, amount, parsed in parsed_rows:
            molar = sum(
                (_atomic_weight(el) * Decimal(str(count)) for el, count in parsed),
                Decimal(0),
            )
            oxygen_atoms = sum(
                (Decimal(str(count)) for el, count in parsed if el == "O"),
                Decimal(0),
            )
            if molar == 0:
                return None
            total += amount * oxygen_atoms * weight / molar
        return total
    row = _CATION_BY_ELEMENT.get(symbol)
    if row is None:
        return None
    weight = _atomic_weight(symbol)
    oxides = {_OXIDE_FEO, _OXIDE_FE2O3} if symbol == "Fe" else {row.oxide}
    total = Decimal(0)
    found = False
    for name, amount, parsed in parsed_rows:
        if name not in oxides:
            continue
        atoms = sum(
            (Decimal(str(count)) for el, count in parsed if el == symbol),
            Decimal(0),
        )
        molar = sum(
            (_atomic_weight(el) * Decimal(str(count)) for el, count in parsed),
            Decimal(0),
        )
        if molar == 0:
            return None
        total += amount * atoms * weight / molar
        found = True
    if not found:
        return None
    return total
