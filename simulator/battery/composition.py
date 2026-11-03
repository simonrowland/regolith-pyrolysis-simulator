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


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None means unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


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
    # Grams of the assemblage the engine charge normalizes, per 100 g of the
    # printed analysis, after Pt and any excluded Cl. Ferric split: FeO_T
    # grams plus the in-range excess oxygen. FeO_T charge: FeO_T grams.
    # None when the row has no cation relabel.
    charge_mass_g_per_100g: Decimal | None = None


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

    # A set refusal stays a refusal. Circularity selects fO2 only inside the
    # Fe2+–Fe3+ span; it must not turn an out-of-range or missing-O row into
    # an engine charge. t-1609: refusal stays a refusal
    if refusal is None and _is_circular_observable(observable) and oxide_moles:
        redox_kind = "conditions"
        split = None

    feo_t_mass = sum(
        (moles * _formula_molar_mass(name) for name, moles in formula_moles.items()),
        Decimal(0),
    )
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
        charge_mass_g_per_100g=_normalized_charge_mass(
            feo_t_mass, excess=excess, redox_kind=redox_kind
        ),
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


def _normalized_charge_mass(
    feo_t_mass: Decimal,
    *,
    excess: Decimal | None,
    redox_kind: str,
) -> Decimal:
    """Grams of the assemblage the engine charge normalizes.

    Premise: the engine charge and the printed analysis describe the same
    100 g. The phase is that charge renormalized to 100 wt%, so the printed
    element wt% is the phase wt% times this mass over 100.

    Algebra: a ferric split replaces each FeO with FeO + Fe2O3. Fe2O3 is
    two FeO plus one oxygen, and n_Fe3+ = 2 * excess, so the added mass is
    excess * M_O. m_ferric = m_FeO_T + excess * M_O. A FeO_T charge
    (conditions, all-ferrous, no iron) carries no extra oxygen, so its
    mass stays m_FeO_T. Units: g per 100 g of the printed analysis, after
    Pt and any excluded Cl.

    Worked echo: 50 g SiO2 + 50 g Fe2O3 is a closed 100 g analysis whose
    excess is n_Fe/2. The ferric charge mass is that 100 g, and an echo
    of the charge returns the printed silicon. The same analysis scored
    as oxygen_excess sends SiO2 + FeO; scaling by the FeO_T mass returns
    the same printed silicon, and adding the excess oxygen onto that
    charge would not. 90 g SiO2 + 9 g FeO + 1 g Pt has no excess, so both
    masses are the 99 g of oxide.
    """

    if redox_kind == "ferric_fraction" and excess is not None:
        return feo_t_mass + excess * _atomic_weight("O")
    return feo_t_mass


def element_wt_percent_on_printed_analysis(
    oxide_wt_percent: Mapping[str, object],
    element: str,
    *,
    charge_mass_g_per_100g: Decimal,
) -> Decimal | None:
    """Element wt% on the printed analysis, from a renormalized engine phase.

    ``charge_mass_g_per_100g`` is :func:`_normalized_charge_mass`: the grams
    of the assemblage that was normalized, not a second oxygen count.
    w_printed = w_phase * (m_charge / 100). Units: (g / 100 g phase) *
    (g charge / 100 g analysis) / (g / 100 g) = g / 100 g analysis.
    """

    phase = element_wt_percent_of_oxide_phase(oxide_wt_percent, element)
    if phase is None:
        return None
    return phase * charge_mass_g_per_100g / Decimal(100)


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
