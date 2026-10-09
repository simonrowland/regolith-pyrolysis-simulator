"""Assign an engine phase's formula or oxide weight percent to oxide moles.

One conversion, built on ``parse_formula``. The MELTS endmember
decomposition used by activity proxies is the same stoichiometry.
AlphaMELTS phase rows call this. The molar-mass parser in the
alphaMELTS adapter and ``composition_projection.py`` stay where they
are; this module does not replace them.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from itertools import product

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.accounting.formulas import parse_formula, resolve_species_formula


# Printed oxide weight percent is not the ledger's 1e-12 kg atom tolerance.
# A relative band accepts ordinary rounding on a batch-scale phase.
# Unit check for the mole step below: kg / (kg/mol) = mol.
# Sanity: 1 kg of SiO2 is 1/0.06008 mol, not 1 mol.
PHASE_OXIDE_MASS_REL_TOLERANCE = 1.0e-3
PHASE_OXIDE_MASS_ABS_TOLERANCE_KG = 1.0e-8

REASON_OXIDATION_AMBIGUOUS = "oxide_oxidation_state_ambiguous"
REASON_UNPARSED_TOKEN = "oxide_assignment_unparsed_token"
REASON_UNKNOWN_ELEMENT = "oxide_assignment_unknown_element"
REASON_MASS_UNCLOSED = "oxide_assignment_mass_unclosed"
REASON_ELEMENT_UNCLOSED = "oxide_assignment_element_unclosed"
REASON_MISSING_COMPOSITION = "oxide_assignment_missing_composition"

# Non-iron cations in the fourteen-oxide basis. Oxide moles per cation,
# and oxygen atoms consumed per cation. FeO and Fe2O3 are assigned from
# the oxygen that remains.
_CATION_OXIDE: dict[str, tuple[str, float, float]] = {
    "Si": ("SiO2", 1.0, 2.0),
    "Ti": ("TiO2", 1.0, 2.0),
    "Al": ("Al2O3", 0.5, 1.5),
    "Mg": ("MgO", 1.0, 1.0),
    "Ca": ("CaO", 1.0, 1.0),
    "Na": ("Na2O", 0.5, 0.5),
    "K": ("K2O", 0.5, 0.5),
    "Cr": ("Cr2O3", 0.5, 1.5),
    "Mn": ("MnO", 1.0, 1.0),
    "P": ("P2O5", 0.5, 2.5),
    "Ni": ("NiO", 1.0, 1.0),
    "Co": ("CoO", 1.0, 1.0),
}

_COUNT_RE = r"(\d+(?:\.\d+)?)"
_FE3_RE = re.compile(r"Fe'''" + _COUNT_RE + r"?")
_FE2_RE = re.compile(r"Fe''" + _COUNT_RE + r"?")
_EMPTY_GROUP_RE = re.compile(r"\(\)(?:\d+(?:\.\d+)?)?")
# Stands in for Fe''' while Fe'' is edited. Not an element, and removed
# before parse_formula sees the text.
_FE3_MARK = "\ue000"
_FORMULA_TOKEN_RE = re.compile(r"\d+\.\d+|\d+|[A-Z][a-z]?|[()\[\]{}]")
_OPEN_GROUP = {"(": ")", "[": "]", "{": "}"}


class OxideAssignmentRefusal(AccountingError):
    """One phase could not be converted. Other phases are unaffected."""

    def __init__(self, *, phase: str, reason: str, token: str) -> None:
        self.phase = phase
        self.reason = reason
        self.token = token
        super().__init__(
            f"{reason}: phase {phase!r} token {token!r}"
        )


@dataclass(frozen=True)
class PhaseOxideAssignment:
    """Oxide moles and kilograms keyed by the engine's phase token."""

    species_mol: dict[str, dict[str, float]]
    species_kg: dict[str, dict[str, float]]
    refusals: tuple[OxideAssignmentRefusal, ...]


def assign_phase_oxides(
    instances: list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
) -> PhaseOxideAssignment:
    """Aggregate instances to a phase and assign oxide moles.

    A positive oxide weight percent is the composition. A formula token
    is used only when that instance has no positive oxide percent. An
    instance that cannot be converted refuses the whole phase. A
    non-positive mass is not a phase row.
    """
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    refusals: list[OxideAssignmentRefusal] = []
    for instance in instances:
        phase = str(instance.get("phase") or "").strip()
        if not phase:
            refusals.append(OxideAssignmentRefusal(
                phase="",
                reason=REASON_MISSING_COMPOSITION,
                token=str(instance.get("instance_id") or ""),
            ))
            continue
        grouped[phase].append(instance)

    species_mol: dict[str, dict[str, float]] = {}
    species_kg: dict[str, dict[str, float]] = {}
    for phase, rows in grouped.items():
        try:
            phase_mol, phase_kg = _assign_one_phase(phase, rows)
        except OxideAssignmentRefusal as refusal:
            refusals.append(refusal)
            continue
        if phase_mol:
            species_mol[phase] = phase_mol
            species_kg[phase] = phase_kg
    return PhaseOxideAssignment(
        species_mol=species_mol,
        species_kg=species_kg,
        refusals=tuple(refusals),
    )


def _assign_one_phase(
    phase: str,
    rows: list[Mapping[str, object]],
) -> tuple[dict[str, float], dict[str, float]]:
    phase_mol: dict[str, float] = defaultdict(float)
    phase_kg: dict[str, float] = defaultdict(float)
    phase_mass_kg = 0.0
    contributed = False
    for instance in rows:
        mass_kg = _instance_mass_kg(phase, instance)
        if mass_kg is None:
            continue
        if mass_kg < 0.0:
            continue
        if mass_kg == 0.0:
            continue
        contributed = True
        phase_mass_kg += mass_kg
        row_mol, row_kg = _assign_instance(phase, instance, mass_kg)
        for oxide, amount in row_mol.items():
            phase_mol[oxide] += amount
        for oxide, amount in row_kg.items():
            phase_kg[oxide] += amount
    if not contributed:
        return {}, {}
    oxide_mass_kg = sum(phase_kg.values())
    if not _mass_closed(oxide_mass_kg, phase_mass_kg):
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MASS_UNCLOSED,
            token="",
        )
    return dict(phase_mol), dict(phase_kg)


def _assign_instance(
    phase: str,
    instance: Mapping[str, object],
    mass_kg: float,
) -> tuple[dict[str, float], dict[str, float]]:
    composition = instance.get("composition_wt_pct") or {}
    positive: dict[str, float] = {}
    if isinstance(composition, Mapping):
        for name, raw in composition.items():
            try:
                wt_pct = float(raw)
            except (TypeError, ValueError) as exc:
                raise OxideAssignmentRefusal(
                    phase=phase,
                    reason=REASON_UNPARSED_TOKEN,
                    token=str(name),
                ) from exc
            if not _finite(wt_pct):
                raise OxideAssignmentRefusal(
                    phase=phase,
                    reason=REASON_UNPARSED_TOKEN,
                    token=str(name),
                )
            if wt_pct > 0.0:
                positive[str(name)] = wt_pct
    if positive:
        return _oxides_from_weight_percent(phase, positive, mass_kg)
    token = str(
        instance.get("formula")
        or instance.get("formula_or_endmember_token")
        or ""
    ).strip()
    if not token:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MISSING_COMPOSITION,
            token="",
        )
    return _oxides_from_formula(phase, token, mass_kg)


def _oxides_from_weight_percent(
    phase: str,
    weight_percent: Mapping[str, float],
    mass_kg: float,
) -> tuple[dict[str, float], dict[str, float]]:
    moles: dict[str, float] = {}
    kilograms: dict[str, float] = {}
    for name, wt_pct in weight_percent.items():
        component_kg = mass_kg * float(wt_pct) / 100.0
        try:
            molar_mass = parse_formula(name).molar_mass_kg_per_mol()
        except AccountingError as exc:
            raise OxideAssignmentRefusal(
                phase=phase,
                reason=REASON_UNPARSED_TOKEN,
                token=name,
            ) from exc
        # kg / (kg/mol) = mol. Sanity: 1 kg SiO2 is about 16.64 mol.
        moles[name] = component_kg / molar_mass
        kilograms[name] = component_kg
    oxide_mass_kg = sum(kilograms.values())
    if not _mass_closed(oxide_mass_kg, mass_kg):
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MASS_UNCLOSED,
            token="",
        )
    return moles, kilograms


@lru_cache(maxsize=None)
def _activity_label_atoms(formula: str) -> tuple[tuple[str, float], ...]:
    """Atoms of one MELTS activity label.

    ``parse_formula`` rejects a decimal coefficient because ``.`` is a
    hydrate boundary. ThermoEngine names such as ``MnSi0.5O2`` have no
    groups, so a complete element-and-number cover is that decimal.
    A parenthesized site fraction is not an activity label and stays
    unresolved. Phase formulas use ``_formula_counts``, which accepts
    groups, decimals, and annotated iron.
    """
    text = str(formula).strip().strip("\"'")
    try:
        atoms = dict(resolve_species_formula(text).atoms)
    except UnknownSpeciesError:
        tokens = list(re.finditer(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)?", text))
        if not tokens or "".join(token.group(0) for token in tokens) != text:
            return ()
        atoms = {}
        for token in tokens:
            element = token.group(1)
            count = float(token.group(2) or 1.0)
            atoms[element] = atoms.get(element, 0.0) + count
    return tuple(sorted(
        (str(element), float(count)) for element, count in atoms.items()
    ))


def _melts_oxide_basis() -> tuple[str, ...]:
    return tuple(entry[0] for entry in _CATION_OXIDE.values()) + ("FeO", "Fe2O3")


@lru_cache(maxsize=None)
def oxide_component_stoichiometry(
    endmember: str,
) -> tuple[tuple[str, float], ...]:
    """Unique MELTS-oxide decomposition of one endmember formula.

    Premise: the endmember atom vector is a sum of oxide vectors on the
    fourteen-oxide basis plus FeO and Fe2O3. For each cation the
    coefficient is that cation's count divided by its count in the
    oxide. Keep the mapping only when one combination reconstructs
    every atom, including oxygen. The coefficient is mol oxide per mol
    endmember. No unique mapping returns an empty tuple; this function
    does not choose a refusal reason.

    Sanity: Na2SiO3 is 1 Na2O + 1 SiO2; KAlSiO4 is 0.5 K2O + 0.5 Al2O3
    + 1 SiO2; MgCr2O4 is 1 MgO + 1 Cr2O3; MnSi0.5O2 is 1 MnO + 0.5 SiO2.
    H2O, Fe3O4, and a name such as ``fo`` return ().
    """
    atoms = dict(_activity_label_atoms(str(endmember)))
    if not atoms or float(atoms.get("O", 0.0)) <= 0.0:
        return ()
    cations = tuple(sorted(element for element in atoms if element != "O"))
    oxide_atoms = {
        oxide: dict(_activity_label_atoms(oxide))
        for oxide in _melts_oxide_basis()
    }
    choices: list[list[tuple[str, float]]] = []
    for cation in cations:
        candidates: list[tuple[str, float]] = []
        for oxide, component_atoms in oxide_atoms.items():
            component_cations = {
                element for element in component_atoms if element != "O"
            }
            if component_cations != {cation}:
                continue
            coefficient = atoms[cation] / component_atoms[cation]
            candidates.append((oxide, coefficient))
        if not candidates:
            return ()
        choices.append(candidates)

    decompositions: list[dict[str, float]] = []
    for combination in product(*choices):
        reconstructed: dict[str, float] = {}
        coefficients: dict[str, float] = {}
        for oxide, coefficient in combination:
            coefficients[oxide] = coefficients.get(oxide, 0.0) + coefficient
            for element, count in oxide_atoms[oxide].items():
                reconstructed[element] = (
                    reconstructed.get(element, 0.0) + coefficient * count
                )
        if set(reconstructed) != set(atoms):
            continue
        if all(
            math.isclose(
                reconstructed[element],
                atoms[element],
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            for element in atoms
        ):
            decompositions.append(coefficients)
    if len(decompositions) != 1:
        return ()
    return tuple(sorted(decompositions[0].items()))


def _oxides_from_formula(
    phase: str,
    token: str,
    mass_kg: float,
) -> tuple[dict[str, float], dict[str, float]]:
    # Annotated iron and an ambiguous formula return no unique
    # decomposition. Those stay on the phase-assignment path below.
    decomposition = oxide_component_stoichiometry(token)
    if decomposition:
        try:
            elements, molar_mass_kg = _formula_counts(token)
        except AccountingError:
            elements, molar_mass_kg = {}, 0.0
        if (
            elements
            and _finite(molar_mass_kg)
            and molar_mass_kg > 0.0
        ):
            return _moles_from_oxide_coefficients(
                phase,
                token,
                mass_kg,
                elements,
                molar_mass_kg,
                dict(decomposition),
            )
    try:
        elements, molar_mass_kg = _formula_counts(_all_iron_formula(token))
        fe3_bare = _formula_counts(
            _iron_subset_formula(token, keep="fe3")
        )[0].get("Fe", 0.0)
        fe2_bare = _formula_counts(
            _iron_subset_formula(token, keep="fe2")
        )[0].get("Fe", 0.0)
    except AccountingError as exc:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_UNPARSED_TOKEN,
            token=token,
        ) from exc
    if not elements or not _finite(molar_mass_kg) or molar_mass_kg <= 0.0:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_UNPARSED_TOKEN,
            token=token,
        )
    unknown = sorted(
        element for element in elements
        if element not in _CATION_OXIDE and element not in {"Fe", "O"}
    )
    if unknown:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_UNKNOWN_ELEMENT,
            token=token,
        )

    fe_total = float(elements.get("Fe", 0.0))
    # A decimal site fraction can leave one iron count a negative
    # 1e-16 while another count is real. Each value is judged alone.
    bare = _zero_negative_roundoff(fe3_bare + fe2_bare - fe_total)
    fe3 = _zero_negative_roundoff(fe3_bare - bare)
    fe2 = _zero_negative_roundoff(fe2_bare - bare)
    if bare < 0.0 or fe2 < 0.0 or fe3 < 0.0:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_OXIDATION_AMBIGUOUS,
            token=token,
        )
    oxygen_left = float(elements.get("O", 0.0))
    oxide_per_formula: dict[str, float] = {}
    for element, (oxide, per_cation, oxygen_per_cation) in _CATION_OXIDE.items():
        count = float(elements.get(element, 0.0))
        if _near(count, 0.0):
            continue
        oxide_per_formula[oxide] = count * per_cation
        oxygen_left -= count * oxygen_per_cation

    annotated = not _near(bare, 0.0) and (
        not _near(fe2, 0.0) or not _near(fe3, 0.0)
    )
    if annotated or (not _near(bare, 0.0) and not _near(fe_total, bare)):
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_OXIDATION_AMBIGUOUS,
            token=token,
        )
    if _near(bare, 0.0) and (
        not _near(fe2, 0.0) or not _near(fe3, 0.0)
    ):
        needed = fe2 * 1.0 + fe3 * 1.5
        if not _near(oxygen_left, needed):
            raise OxideAssignmentRefusal(
                phase=phase,
                reason=REASON_OXIDATION_AMBIGUOUS,
                token=token,
            )
        if not _near(fe2, 0.0):
            oxide_per_formula["FeO"] = fe2
        if not _near(fe3, 0.0):
            oxide_per_formula["Fe2O3"] = fe3 / 2.0
    else:
        feo_oxygen = fe_total * 1.0
        fe2o3_oxygen = fe_total * 1.5
        feo_match = _near(oxygen_left, feo_oxygen)
        fe2o3_match = _near(oxygen_left, fe2o3_oxygen)
        if feo_match and fe2o3_match:
            if not _near(fe_total, 0.0):
                raise OxideAssignmentRefusal(
                    phase=phase,
                    reason=REASON_OXIDATION_AMBIGUOUS,
                    token=token,
                )
        elif feo_match:
            if not _near(fe_total, 0.0):
                oxide_per_formula["FeO"] = fe_total
        elif fe2o3_match:
            oxide_per_formula["Fe2O3"] = fe_total / 2.0
        else:
            raise OxideAssignmentRefusal(
                phase=phase,
                reason=REASON_OXIDATION_AMBIGUOUS,
                token=token,
            )

    return _moles_from_oxide_coefficients(
        phase,
        token,
        mass_kg,
        elements,
        molar_mass_kg,
        oxide_per_formula,
    )


def _moles_from_oxide_coefficients(
    phase: str,
    token: str,
    mass_kg: float,
    elements: Mapping[str, float],
    molar_mass_kg: float,
    oxide_per_formula: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    formula_moles = mass_kg / molar_mass_kg
    moles: dict[str, float] = {}
    kilograms: dict[str, float] = {}
    for oxide, per_formula in oxide_per_formula.items():
        amount = formula_moles * per_formula
        if _near(amount, 0.0):
            continue
        moles[oxide] = amount
        kilograms[oxide] = amount * parse_formula(oxide).molar_mass_kg_per_mol()

    got: dict[str, float] = defaultdict(float)
    for oxide, amount in moles.items():
        for element, count in parse_formula(oxide).elements.items():
            got[element] += float(count) * amount
    for element, count in elements.items():
        if not _near(got.get(element, 0.0), float(count) * formula_moles):
            raise OxideAssignmentRefusal(
                phase=phase,
                reason=REASON_ELEMENT_UNCLOSED,
                token=token,
            )
    for element, amount in got.items():
        if element not in elements and not _near(amount, 0.0):
            raise OxideAssignmentRefusal(
                phase=phase,
                reason=REASON_ELEMENT_UNCLOSED,
                token=token,
            )
    oxide_mass_kg = sum(kilograms.values())
    if not _mass_closed(oxide_mass_kg, mass_kg):
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MASS_UNCLOSED,
            token=token,
        )
    return moles, kilograms


def _instance_mass_kg(
    phase: str,
    instance: Mapping[str, object],
) -> float | None:
    raw = instance.get("physical_mass_kg")
    if raw is None:
        raw = instance.get("mass_kg")
    if raw is None:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MISSING_COMPOSITION,
            token=str(instance.get("instance_id") or ""),
        )
    try:
        mass_kg = float(raw)
    except (TypeError, ValueError) as exc:
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_UNPARSED_TOKEN,
            token=str(instance.get("instance_id") or ""),
        ) from exc
    if not _finite(mass_kg):
        raise OxideAssignmentRefusal(
            phase=phase,
            reason=REASON_MASS_UNCLOSED,
            token=str(instance.get("instance_id") or ""),
        )
    return mass_kg


class _FormulaShapeError(Exception):
    """The token is not a parenthesized formula this rewrite can clear."""


class _FormulaSeq:
    __slots__ = ("leading", "items")

    def __init__(
        self,
        leading: str | None,
        items: list[tuple[object, ...]],
    ) -> None:
        self.leading = leading
        self.items = items


def _formula_counts(token: str) -> tuple[dict[str, float], float]:
    """Element counts and molar mass (kg/mol) for one formula token.

    An empty token (an iron subset that dropped every atom) is zero iron,
    not a parse failure. The caller refuses an empty all-iron formula.
    """
    text = token.strip()
    if not text:
        return {}, 0.0
    formula, scale = _integer_coefficient_formula(text)
    parsed = parse_formula(formula)
    if scale == 1:
        return dict(parsed.elements), parsed.molar_mass_kg_per_mol()
    divisor = Decimal(scale)
    elements = {
        element: float(Decimal(count) / divisor)
        for element, count in parsed.elements.items()
    }
    return elements, parsed.molar_mass_kg_per_mol() / float(scale)


def _integer_coefficient_formula(token: str) -> tuple[str, int]:
    """Clear decimal coefficients so ``parse_formula`` can count the atoms.

    ``parse_formula`` splits on ``.`` because that dot is a hydrate boundary
    (``CuSO4.5H2O`` is five waters). A MELTS site fraction is a decimal
    coefficient, so this rewrite multiplies atom coefficients by
    ``10**places`` and the caller divides the parsed counts by that scale.
    Group multipliers stay at their original value: scaling them as well
    would count a nested coefficient twice. A decimal multiplier is written
    as an integer, and the scale grows by one power of ten, so atoms
    outside that group stay on the same divisor.
    """
    if "." not in token:
        return token, 1
    pieces = _FORMULA_TOKEN_RE.findall(token)
    if "".join(pieces) != token:
        return token, 1
    try:
        sequence, index = _read_formula_sequence(
            pieces, 0, None, allow_leading=True,
        )
        if index != len(pieces):
            raise _FormulaShapeError(token)
        places = _max_decimal_places(sequence)
        if places == 0:
            return token, 1
        base = 10 ** places
        depth = _max_decimal_multiplier_depth(sequence, 0)
        exponent = depth + 1
        rewritten = _emit_formula(sequence, 0, base, exponent)
    except _FormulaShapeError:
        return token, 1
    return rewritten, base ** exponent


def _read_formula_sequence(
    pieces: list[str],
    index: int,
    stop: str | None,
    *,
    allow_leading: bool,
) -> tuple[_FormulaSeq, int]:
    leading = None
    if (
        allow_leading
        and index < len(pieces)
        and _is_number_token(pieces[index])
    ):
        leading = pieces[index]
        index += 1
        if index >= len(pieces) or pieces[index] == stop:
            raise _FormulaShapeError(leading)
    items: list[tuple[object, ...]] = []
    while index < len(pieces) and pieces[index] != stop:
        token = pieces[index]
        if token in _OPEN_GROUP:
            close = _OPEN_GROUP[token]
            inner, index = _read_formula_sequence(
                pieces, index + 1, close, allow_leading=False,
            )
            if index >= len(pieces) or pieces[index] != close:
                raise _FormulaShapeError(token)
            index += 1
            if index < len(pieces) and _is_number_token(pieces[index]):
                multiplier = pieces[index]
                index += 1
            else:
                multiplier = "1"
            items.append(("group", inner, multiplier))
            continue
        if _is_element_token(token):
            index += 1
            if index < len(pieces) and _is_number_token(pieces[index]):
                coefficient = pieces[index]
                index += 1
            else:
                coefficient = "1"
            items.append(("atom", token, coefficient))
            continue
        raise _FormulaShapeError(token)
    if not items:
        raise _FormulaShapeError(stop or "")
    return _FormulaSeq(leading, items), index


def _max_decimal_places(sequence: _FormulaSeq) -> int:
    best = _decimal_places(sequence.leading)
    for item in sequence.items:
        if item[0] == "atom":
            best = max(best, _decimal_places(str(item[2])))
            continue
        _kind, inner, multiplier = item
        assert isinstance(inner, _FormulaSeq)
        best = max(
            best,
            _decimal_places(str(multiplier)),
            _max_decimal_places(inner),
        )
    return best


def _max_decimal_multiplier_depth(sequence: _FormulaSeq, entering: int) -> int:
    depth = entering + (1 if _is_decimal_number(sequence.leading) else 0)
    best = 0
    for item in sequence.items:
        if item[0] == "atom":
            best = max(best, depth)
            continue
        _kind, inner, multiplier = item
        assert isinstance(inner, _FormulaSeq)
        child = depth + (1 if _is_decimal_number(str(multiplier)) else 0)
        best = max(best, _max_decimal_multiplier_depth(inner, child))
    return best


def _emit_formula(
    sequence: _FormulaSeq,
    entering: int,
    base: int,
    exponent: int,
) -> str:
    depth = entering + (1 if _is_decimal_number(sequence.leading) else 0)
    parts: list[str] = []
    if sequence.leading is not None:
        parts.append(_emit_multiplier(sequence.leading, base))
    for item in sequence.items:
        if item[0] == "atom":
            _kind, element, coefficient = item
            parts.append(
                f"{element}{_scale_coefficient(str(coefficient), base, exponent - depth)}"
            )
            continue
        _kind, inner, multiplier = item
        assert isinstance(inner, _FormulaSeq)
        child = depth + (1 if _is_decimal_number(str(multiplier)) else 0)
        body = _emit_formula(inner, child, base, exponent)
        parts.append(f"({body}){_emit_multiplier(str(multiplier), base)}")
    return "".join(parts)


def _scale_coefficient(text: str, base: int, power: int) -> str:
    value = Decimal(text) * (Decimal(base) ** power)
    return _exact_positive_integer(value)


def _emit_multiplier(text: str, base: int) -> str:
    value = Decimal(text)
    if "." in text:
        value *= Decimal(base)
    integer = _exact_positive_integer(value)
    if integer == "1":
        return ""
    return integer


def _exact_positive_integer(value: Decimal) -> str:
    integral = value.to_integral_value()
    if value != integral or integral <= 0:
        raise _FormulaShapeError(str(value))
    return str(int(integral))


def _decimal_places(text: str | None) -> int:
    if not text or "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _is_decimal_number(text: str | None) -> bool:
    return bool(text) and "." in str(text)


def _is_number_token(token: str) -> bool:
    return token[:1].isdigit()


def _is_element_token(token: str) -> bool:
    return token[:1].isupper()


def _all_iron_formula(token: str) -> str:
    return _clean_empty_groups(
        _FE2_RE.sub(_keep_count("Fe"), _FE3_RE.sub(_keep_count("Fe"), token))
    )


def _iron_subset_formula(token: str, *, keep: str) -> str:
    if keep == "fe3":
        protected = _FE3_RE.sub(_keep_count(_FE3_MARK), token)
        dropped = _FE2_RE.sub("", protected)
        restored = dropped.replace(_FE3_MARK, "Fe")
        return _clean_empty_groups(restored)
    dropped = _FE3_RE.sub("", token)
    restored = _FE2_RE.sub(_keep_count("Fe"), dropped)
    return _clean_empty_groups(restored)


def _keep_count(replacement: str):
    def replacer(match: re.Match[str]) -> str:
        return replacement + (match.group(1) or "")

    return replacer


def _clean_empty_groups(formula: str) -> str:
    previous = None
    text = formula
    while text != previous:
        previous = text
        text = _EMPTY_GROUP_RE.sub("", text)
    return text


def _mass_closed(oxide_kg: float, mass_kg: float) -> bool:
    return abs(oxide_kg - mass_kg) <= max(
        PHASE_OXIDE_MASS_ABS_TOLERANCE_KG,
        PHASE_OXIDE_MASS_REL_TOLERANCE * mass_kg,
    )


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _near(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= max(1.0e-9, 1.0e-8 * scale)


def _zero_negative_roundoff(value: float) -> float:
    if value < 0.0 and _near(value, 0.0):
        return 0.0
    return value
