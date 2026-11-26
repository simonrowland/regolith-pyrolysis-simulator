"""Species formula parsing and molar mass helpers."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.chemistry.formula_text import fold_subscript_digits
from simulator.yaml_cache import load_cached_safe_yaml


# Abridged standard atomic weights for normal terrestrial materials.
# Source: CIAAW standard atomic weights / NIST periodic-table values.
ATOMIC_WEIGHTS_G_PER_MOL = MappingProxyType(
    {
        "H": 1.008,
        "He": 4.002602,
        "Li": 6.94,
        "Be": 9.0121831,
        "B": 10.81,
        "C": 12.011,
        "N": 14.007,
        "O": 15.999,
        "F": 18.998403163,
        "Ne": 20.1797,
        "Na": 22.98976928,
        "Mg": 24.305,
        "Al": 26.9815385,
        "Si": 28.085,
        "P": 30.973761998,
        "S": 32.06,
        "Cl": 35.45,
        "Ar": 39.948,
        "K": 39.0983,
        "Ca": 40.078,
        "Sc": 44.955908,
        "Ti": 47.867,
        "V": 50.9415,
        "Cr": 51.9961,
        "Mn": 54.938044,
        "Fe": 55.845,
        "Co": 58.933194,
        "Ni": 58.6934,
        "Cu": 63.546,
        "Zn": 65.38,
        "Ga": 69.723,
        "Ge": 72.63,
        "As": 74.921595,
        "Se": 78.971,
        "Br": 79.904,
        "Kr": 83.798,
        "Rb": 85.4678,
        "Sr": 87.62,
        "Y": 88.90584,
        "Zr": 91.224,
        "Nb": 92.90637,
        "Mo": 95.95,
        "Ru": 101.07,
        "Rh": 102.9055,
        "Pd": 106.42,
        "Ag": 107.8682,
        "Cd": 112.414,
        "In": 114.818,
        "Sn": 118.71,
        "Sb": 121.76,
        "Te": 127.6,
        "I": 126.90447,
        "Xe": 131.293,
        "Cs": 132.90545196,
        "Ba": 137.327,
        "La": 138.90547,
        "Ce": 140.116,
        "Pr": 140.90766,
        "Nd": 144.242,
        "Sm": 150.36,
        "Eu": 151.964,
        "Gd": 157.25,
        "Tb": 158.92535,
        "Dy": 162.5,
        "Ho": 164.93033,
        "Er": 167.259,
        "Tm": 168.93422,
        "Yb": 173.045,
        "Lu": 174.9668,
        "Hf": 178.49,
        "Ta": 180.94788,
        "W": 183.84,
        "Re": 186.207,
        "Os": 190.23,
        "Ir": 192.217,
        "Pt": 195.084,
        "Au": 196.966569,
        "Hg": 200.592,
        "Tl": 204.38,
        "Pb": 207.2,
        "Bi": 208.9804,
        "Th": 232.0377,
        "U": 238.02891,
    }
)

MOLAR_MASS_ABS_TOLERANCE_G_MOL = 1e-9
MOLAR_MASS_REL_TOLERANCE = 1e-6
DIAMOND_POLYMORPH_REFUSAL_REASON = (
    "diamond polymorph is not represented by the formula identity"
)

_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {v: k for k, v in _OPEN_TO_CLOSE.items()}
_NUMBER_RE = re.compile(r"(?:\d+/\d+|\d+(?:\.\d+)?|\.\d+)")
_FORMULA_PHASE_SUFFIXES = {
    "aq": "aq",
    "c": "cr",
    "cr": "cr",
    "g": "g",
    "gl": "gl",
    "glass": "gl",
    "l": "l",
    "liq": "l",
    "liquid": "l",
    "solid": "cr",
    "s": "cr",
    "gas": "g",
    "vapor": "g",
}
_FORMULA_PHASE_SUFFIX_LABELS = tuple(_FORMULA_PHASE_SUFFIXES)
_FORMULA_PHASE_SUFFIX_TOKEN = "(?:" + "|".join(
    re.escape(label) for label in _FORMULA_PHASE_SUFFIX_LABELS
) + ")"
_PHASE_SUFFIX_RE = re.compile(
    rf"(?:\((?P<paren>{_FORMULA_PHASE_SUFFIX_TOKEN})\)|"
    rf"\[(?P<bracket>{_FORMULA_PHASE_SUFFIX_TOKEN})\])$",
    re.IGNORECASE,
)
_RAW_PHASE_SUFFIX_RE = re.compile(
    rf"(?:\(\s*(?P<paren>{_FORMULA_PHASE_SUFFIX_TOKEN})\s*\)|"
    rf"\[\s*(?P<bracket>{_FORMULA_PHASE_SUFFIX_TOKEN})\s*\])\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpeciesFormula:
    """Atom counts and molar mass for one catalog species."""

    species: str
    elements: Mapping[str, float]
    molar_mass_g_mol: float | None = None
    estimated: bool = False
    requires_feedstock_metadata: bool = False
    source: str = ""

    def __post_init__(self) -> None:
        species = str(self.species).strip()
        if not species:
            raise UnknownSpeciesError("species id is required")

        normalized: dict[str, float] = {}
        for element, count in dict(self.elements).items():
            symbol = str(element).strip()
            if symbol not in ATOMIC_WEIGHTS_G_PER_MOL:
                raise UnknownSpeciesError(
                    f"unknown atomic weight for element {symbol!r} in {species!r}"
                )
            value = float(count)
            if not math.isfinite(value) or value <= 0.0:
                raise AccountingError(
                    f"atom count for {symbol!r} in {species!r} must be positive"
                )
            normalized[symbol] = normalized.get(symbol, 0.0) + value

        if not normalized:
            raise UnknownSpeciesError(f"species {species!r} has no elements")

        derived_molar_mass = sum(
            ATOMIC_WEIGHTS_G_PER_MOL[element] * count
            for element, count in normalized.items()
        )
        declared = derived_molar_mass if self.molar_mass_g_mol is None else float(self.molar_mass_g_mol)
        if not math.isfinite(declared) or declared <= 0.0:
            raise AccountingError(f"molar mass for {species!r} must be positive")
        if not _close_enough(
            derived_molar_mass,
            declared,
            MOLAR_MASS_ABS_TOLERANCE_G_MOL,
            MOLAR_MASS_REL_TOLERANCE,
        ):
            raise AccountingError(
                f"declared molar mass for {species!r} does not match formula: "
                f"declared={declared:.12g} g/mol derived={derived_molar_mass:.12g} g/mol"
            )

        object.__setattr__(self, "species", species)
        object.__setattr__(self, "elements", MappingProxyType(dict(sorted(normalized.items()))))
        object.__setattr__(self, "molar_mass_g_mol", derived_molar_mass)
        object.__setattr__(self, "estimated", bool(self.estimated))
        object.__setattr__(
            self,
            "requires_feedstock_metadata",
            bool(self.requires_feedstock_metadata),
        )
        object.__setattr__(self, "source", str(self.source or ""))

    @classmethod
    def parse(cls, formula: str, species: str | None = None) -> "SpeciesFormula":
        return parse_formula(formula, species=species)

    @classmethod
    def from_atoms(
        cls,
        species: str,
        atoms: Mapping[str, float],
        *,
        molar_mass_g_mol: float | None = None,
        estimated: bool = False,
        requires_feedstock_metadata: bool = False,
        source: str = "",
    ) -> "SpeciesFormula":
        return cls(
            species=species,
            elements=atoms,
            molar_mass_g_mol=molar_mass_g_mol,
            estimated=estimated,
            requires_feedstock_metadata=requires_feedstock_metadata,
            source=source,
        )

    @property
    def name(self) -> str:
        return self.species

    @property
    def atoms(self) -> Mapping[str, float]:
        return self.elements

    def molar_mass_g_per_mol(
        self, atomic_weights: Mapping[str, float] = ATOMIC_WEIGHTS_G_PER_MOL
    ) -> float:
        if atomic_weights is ATOMIC_WEIGHTS_G_PER_MOL:
            return self.molar_mass_g_mol
        return sum(atomic_weights[element] * count for element, count in self.elements.items())

    def molar_mass_kg_per_mol(
        self, atomic_weights: Mapping[str, float] = ATOMIC_WEIGHTS_G_PER_MOL
    ) -> float:
        return self.molar_mass_g_per_mol(atomic_weights) / 1000.0

    def atom_moles(self, species_moles: float) -> dict[str, float]:
        moles = float(species_moles)
        return {element: count * moles for element, count in self.elements.items()}


def parse_formula(
    formula: str,
    species: str | None = None,
    *,
    name: str | None = None,
) -> SpeciesFormula:
    """Parse formula text and return a SpeciesFormula.

    Element symbols and capitalization are exact here. A source spelling that
    needs case folding or OCR correction may enter the formula path only via
    ``_canonicalize_formula_from_printed_weight``: it requires a printed
    formula weight and a parsed-mass match within max(0.05 g/mol, 0.02%).
    This keeps case repair at the source acceptance point instead of guessing
    from a later score-time formula string.

    An ASCII decimal may omit its leading zero after an element, as in
    ``Fe.875S``, ``Fe.90S``, and ``Fe.95O``. The ASCII-dot rule treats a dot
    as an adduct only when every separated segment contains at least two
    distinct integer-count elements; here the left segment is the single
    element Fe, so the dot is its decimal point.

    The t-1795 census's exact-case spellings accepted without repair are
    ``H2S2(g)``, ``H2SO4(l)``, ``H2SO4(g)``, ``$(NH_{4})_{2}CO_{3}(aq)$``,
    ``$H_3AsO_3(aq)$``, ``$H_3AsO_4(aq)$``, ``$NH_{4}HCO_{3}(aq)$``,
    ``$NH_{4}OH(aq)$``, ``$PCl_{3}(l)$``, ``$SO_3(l)$``,
    ``$\\mathrm{CdSO}_{4}\\cdot8/3\\mathrm{H}_{2}\\mathrm{O}(c)$``,
    ``H2AsO2(aq)``, ``H2CO3(aq)``, ``H2N2O2(aq)``, ``H2S(aq)``,
    ``H2S2O8(aq)``, ``H2SO3(aq)``, ``H2SO4(aq)``, ``H2Se(aq)``,
    ``H2SeO3(aq)``, ``H2SeO4(aq)``, ``H3PO4(aq)``, ``H3PO4·1/2H2O``,
    ``HCN(aq)``, ``HCNO(aq)``, ``Na2B4O7(gl)``,
    ``Ca1.02Na0.14K.01(Al2.18Si6.82O18) · 7.33H2O``, ``Fe.875S``, and
    ``Fe.90S``. Their element capitalization and formula delimiters specify
    one tokenization.

    The source rows' 17 accepted case/OCR spellings are ``CaFe2o4``,
    ``CaMgSio4``, ``CuFes2``, ``Fe. 875 s``, ``Fe.94 7o``, ``MgFe2o4``,
    ``Na2so4·10H2o``, ``NaAlSio4``, ``Naf``, ``ZnFe2o4``, ``cawo4``,
    ``cao``, ``srso4``, ``u``, ``v``, ``w``, and ``ws2``. Each has one
    element-symbol tokenization; the source acceptance path admits it only
    when the parsed mass matches the same row's printed formula weight within
    max(0.05 g/mol, 0.02%). Ambiguous runs such as ``coo`` (CoO or CO2),
    ``caso4`` (CaSO4 or CAsO4), and ``cu`` (Cu or C+U) remain refused even
    when a weight is present. A repeated terminal oxygen written as lower-case
    ``o`` after a counted oxygen is refused because it can be an OCR degree
    mark (``H4SiO4o``), not another oxygen.
    An unrecognized all-lowercase terminal parenthesis remains a label or
    ambiguity instead of being tokenized as elements (for example ``S(mon)``).
    Middle-dot hydrates, including fractional coefficients such as
    ``H3PO4·1/2H2O``, Unicode subscripts, simple ``\\mathrm`` subscripts,
    and explicit oxide decimals such as ``FeO1.5`` retain their printed
    counts. A recognized phase suffix is removed for atom counting; callers
    that carry a separate phase must store it on that identity axis.
    The existing bullet separator spelling is accepted only between formula
    segments; empty trailing or repeated separators are rejected.

    Terminal ``(s)``, ``(l)``, ``(g)``, ``(aq)``, ``(c)``, ``(cr)``,
    ``(gl)``, ``(glass)``, ``(liq)``, ``(liquid)``, ``(solid)``, ``(gas)``,
    ``(vapor)``, and their supported bracket forms are phase labels; ``[c]``
    is the crystal phase, not a carbon atom. Formula groups such as
    ``[Al(OH)4]`` remain stoichiometry. TeX, Unicode, middle-dot hydrates,
    and oxide decimals retain their explicit counts. Isotopes, charges,
    names, mixtures, unweighted case variants, and non-formula labels remain
    outside the accepted set. Bare lower-case ``c`` remains refused because
    the source identifies diamond only by its mineral name.
    """

    cleaned = _clean_formula_text(formula)
    if not cleaned:
        raise UnknownSpeciesError("formula is required")
    if name and re.search(r"\bdiamond\b", name, re.IGNORECASE):
        raise UnknownSpeciesError(DIAMOND_POLYMORPH_REFUSAL_REASON)
    previous = None
    while cleaned and previous != cleaned:
        previous = cleaned
        cleaned, suffix = _strip_terminal_phase_suffix(cleaned)
        if suffix is not None:
            cleaned = _clean_formula_text(cleaned)
    if re.search(r"\([a-z][a-z0-9,.\-]*\)$", cleaned):
        raise UnknownSpeciesError(
            f"lowercase parenthetical suffix in {formula!r} is not a formula group"
        )
    return _parse_formula_counts(cleaned, formula, species, name)


def _strip_terminal_phase_suffix(formula: str) -> tuple[str, str | None]:
    """Return a formula without one recognized terminal phase label.

    Suffix membership belongs here so parsing, migration, scoring, and rail
    diagnostics agree. Charge-bearing formula bodies such as ``Ag+(aq)`` are
    deliberately left untouched.
    """

    raw = str(formula).strip()
    math_wrapped = raw.startswith("$") and raw.endswith("$")
    inner = raw[1:-1] if math_wrapped else raw
    cleaned = _clean_formula_text(inner)
    match = _PHASE_SUFFIX_RE.search(cleaned)
    if match is None or cleaned[: match.start()].endswith(("+", "-")):
        return formula, None
    label = match.group("paren") or match.group("bracket")
    if label is None:
        return formula, None
    raw_match = _RAW_PHASE_SUFFIX_RE.search(inner)
    if raw_match is None:
        stem = cleaned[: match.start()]
    else:
        stem = inner[: raw_match.start()].rstrip()
    if math_wrapped:
        stem = f"${stem}$"
    return stem, _FORMULA_PHASE_SUFFIXES[label.casefold()]


def _canonicalize_formula_from_printed_weight(
    formula: str,
    printed_weight_g_mol: object,
    *,
    name: str | None = None,
    repaired: bool = False,
) -> tuple[str | None, str]:
    """Accept source formula spellings only against their own printed mass.

    ``repaired`` marks an explicit source transcription or raster correction.
    A spelling that parses exactly is retained without a weight only when it
    needs no repair. Whenever a printed weight exists, exact and repaired
    candidates must both match it. The return reason is suitable for source
    evidence and typed-refusal diagnostics.
    """

    cleaned = _clean_formula_text(formula)
    if not cleaned:
        return None, "formula is empty"
    body, phase_suffix = _strip_terminal_phase_suffix(cleaned)
    body = _clean_formula_text(body)
    if re.search(r"\([a-z][a-z0-9,.\-]*\)$", body):
        return None, "unrecognized parenthetical suffix"
    try:
        parse_formula(body, name=name)
        normalized = body
    except AccountingError as exc:
        if DIAMOND_POLYMORPH_REFUSAL_REASON in str(exc):
            return None, str(exc)
        try:
            normalized = _unambiguous_element_case(body)
        except UnknownSpeciesError as exc:
            return None, f"ambiguous element case: {exc}"
    case_changed = normalized != body
    repaired_or_case_folded = repaired or case_changed
    if not repaired_or_case_folded:
        candidate = formula
    else:
        candidate = normalized
        if phase_suffix is not None:
            raw_formula = (
                cleaned[1:-1]
                if cleaned.startswith("$") and cleaned.endswith("$")
                else cleaned
            )
            raw_suffix = _RAW_PHASE_SUFFIX_RE.search(raw_formula)
            candidate += (
                raw_suffix.group(0)
                if raw_suffix is not None
                else f"({phase_suffix})"
            )
    if printed_weight_g_mol is None or isinstance(printed_weight_g_mol, bool):
        if repaired_or_case_folded:
            return None, "case/OCR repair has no printed formula weight"
        try:
            parse_formula(candidate, name=name)
        except AccountingError as exc:
            return None, f"exact-case formula did not parse: {exc}"
        return candidate, "accepted exact-case formula"
    try:
        printed_mass = float(printed_weight_g_mol)
    except (TypeError, ValueError):
        return None, "printed formula weight is not numeric"
    if not math.isfinite(printed_mass) or printed_mass <= 0.0:
        return None, "printed formula weight is not positive and finite"
    try:
        parsed = parse_formula(candidate, name=name)
    except AccountingError as exc:
        return None, f"formula did not parse: {exc}"
    difference = abs(parsed.molar_mass_g_mol - printed_mass)
    tolerance = max(0.05, abs(printed_mass) * 0.0002)
    if difference > tolerance:
        return None, (
            f"parsed mass {parsed.molar_mass_g_mol:.6f} g/mol differs from printed "
            f"{printed_mass:.6f} g/mol by {difference:.6f} g/mol "
            f"(tolerance {tolerance:.6f})"
        )
    return candidate, (
        f"parsed mass {parsed.molar_mass_g_mol:.6f} g/mol matches printed "
        f"{printed_mass:.6f} g/mol (difference {difference:.6f}; "
        f"tolerance {tolerance:.6f})"
    )


def _parse_formula_counts(
    cleaned: str,
    original_formula: str,
    species: str | None,
    name: str | None,
) -> SpeciesFormula:
    totals: defaultdict[str, float] = defaultdict(float)
    for segment in _split_formula_segments(cleaned):
        multiplier, body = _leading_multiplier(segment)
        parser = _FormulaParser(body)
        elements = parser.parse()
        for element, count in elements.items():
            totals[element] += count * multiplier

    species_id = species or name or str(original_formula).strip()
    return SpeciesFormula(species=species_id, elements=totals)


def _unambiguous_element_case(formula: str) -> str:
    """Normalize each letter run only when its element segmentation is unique."""

    symbols = tuple(
        sorted(ATOMIC_WEIGHTS_G_PER_MOL, key=lambda symbol: (-len(symbol), symbol))
    )

    @lru_cache(maxsize=None)
    def tokenizations(text: str, index: int) -> tuple[tuple[str, ...], ...]:
        if index == len(text):
            return ((),)
        found: list[tuple[str, ...]] = []
        for symbol in symbols:
            end = index + len(symbol)
            token = text[index:end]
            if token.casefold() != symbol.casefold():
                continue
            if any(char.isupper() for char in text):
                if len(symbol) == 1 and token.islower() and end != len(text):
                    continue
                if len(symbol) == 2 and not (
                    token[0].isupper() and token[1].islower()
                ):
                    continue
            for tail in tokenizations(text, end):
                candidate = (symbol, *tail)
                if candidate not in found:
                    found.append(candidate)
                    if len(found) == 2:
                        return tuple(found)
        return tuple(found)

    def normalize_run(match: re.Match[str]) -> str:
        options = tokenizations(match.group(), 0)
        if len(options) != 1:
            raise UnknownSpeciesError(
                f"element case in {match.group()!r} is not uniquely tokenized"
            )
        return "".join(options[0])

    return re.sub(r"[A-Za-z]+", normalize_run, formula)


def atoms_parse_formula_else_janaf(
    formula: str,
) -> tuple[tuple[str, float], ...] | None:
    """Atom counts for one component name.

    A successful ``parse_formula`` wins. A hydrate stays waters
    (``CuSO4.5H2O`` and ``CuSO4·5H2O`` are five waters, not decimal
    oxygen). A printed decimal subscript stays one species and is not
    integerised (``FeO1.5``, ``KO0.5``, ``Fe0.947O``). After an accounting
    parse failure, JANAF fallback is allowed only when element case is already
    exact; case-foldable and ambiguous spellings stay refused without the
    source-level printed-weight gate. None means both paths failed.
    Other exceptions propagate.
    """

    try:
        parsed = parse_formula(formula)
    except AccountingError:
        body, _phase = _strip_terminal_phase_suffix(formula)
        cleaned_body = _clean_formula_text(body)
        from simulator.reference_data.janaf import (
            formula_composition,
            formula_normalised,
        )

        janaf_formula = formula_normalised(cleaned_body)
        source_letters = re.sub(r"[^A-Za-z]", "", cleaned_body)
        janaf_letters = re.sub(r"[^A-Za-z]", "", janaf_formula)
        if source_letters != janaf_letters:
            return None

        return formula_composition(formula)
    return tuple(
        sorted(
            (str(element), float(count))
            for element, count in parsed.elements.items()
        )
    )


def coerce_species_formula(species: str, value: Any | None = None) -> SpeciesFormula:
    """Convert registry entries into SpeciesFormula objects."""

    if isinstance(value, SpeciesFormula):
        return value
    if value is None:
        return parse_formula(species, species=species)
    if isinstance(value, str):
        return parse_formula(value, species=species)
    if isinstance(value, Mapping):
        estimated = bool(value.get("estimated", False))
        requires_feedstock_metadata = bool(
            value.get("requires_feedstock_metadata", False)
        )
        source = str(value.get("source", ""))
        declared_molar_mass = _declared_molar_mass(value)
        if "atoms" in value:
            atoms = value["atoms"]
            if not isinstance(atoms, Mapping):
                raise UnknownSpeciesError(f"atoms entry for {species!r} must be a mapping")
            return SpeciesFormula.from_atoms(
                species,
                atoms,
                molar_mass_g_mol=declared_molar_mass,
                estimated=estimated,
                requires_feedstock_metadata=requires_feedstock_metadata,
                source=source,
            )
        if "elements" in value:
            elements = value["elements"]
            if not isinstance(elements, Mapping):
                raise UnknownSpeciesError(f"elements entry for {species!r} must be a mapping")
            return SpeciesFormula.from_atoms(
                species,
                elements,
                molar_mass_g_mol=declared_molar_mass,
                estimated=estimated,
                requires_feedstock_metadata=requires_feedstock_metadata,
                source=source,
            )
        mass_fractions = (
            value.get("atom_mass_fractions")
            or value.get("element_mass_fractions")
        )
        if mass_fractions is not None:
            if not isinstance(mass_fractions, Mapping):
                raise UnknownSpeciesError(
                    f"atom_mass_fractions entry for {species!r} must be a mapping"
                )
            return SpeciesFormula.from_atoms(
                species,
                _atoms_from_mass_fractions(species, mass_fractions),
                molar_mass_g_mol=declared_molar_mass,
                estimated=estimated,
                requires_feedstock_metadata=requires_feedstock_metadata,
                source=source,
            )
        if "formula" in value:
            parsed = parse_formula(str(value["formula"]), species=species)
            return SpeciesFormula(
                species=species,
                elements=parsed.elements,
                molar_mass_g_mol=declared_molar_mass,
                estimated=estimated,
                requires_feedstock_metadata=requires_feedstock_metadata,
                source=source,
            )
        if all(str(element) in ATOMIC_WEIGHTS_G_PER_MOL for element in value):
            return SpeciesFormula.from_atoms(species, value)
    raise UnknownSpeciesError(f"formula entry for {species!r} is not supported")


def load_species_formulas(source: str | Path | Mapping[str, Any]) -> dict[str, SpeciesFormula]:
    """Load a species registry from a mapping or YAML file."""

    data = _load_formula_source(source)
    entries = _extract_formula_entries(data)
    _validate_case_aliases(entries)
    return {species: coerce_species_formula(species, spec) for species, spec in entries.items()}


def _atoms_from_mass_fractions(
    species: str, mass_fractions: Mapping[str, Any]
) -> dict[str, float]:
    """Convert element mass fractions into atom-count ratios.

    Feedstock-local mixed species are intentionally explicit; accept common
    bases only so YAML typos fail instead of being silently renormalized.
    """
    parsed: list[tuple[str, float]] = []
    total_fraction = 0.0
    for element, raw_fraction in mass_fractions.items():
        symbol = str(element).strip()
        if symbol not in ATOMIC_WEIGHTS_G_PER_MOL:
            raise UnknownSpeciesError(
                f"unknown atomic weight for element {symbol!r} in {species!r}"
            )
        fraction = float(raw_fraction)
        if not math.isfinite(fraction) or fraction <= 0.0:
            raise AccountingError(
                f"mass fraction for {symbol!r} in {species!r} must be positive"
            )
        parsed.append((symbol, fraction))
        total_fraction += fraction
    if not parsed:
        raise UnknownSpeciesError(
            f"atom_mass_fractions for {species!r} must not be empty"
        )
    basis = next(
        (
            candidate
            for candidate in (1.0, 100.0, 1000.0)
            if math.isclose(total_fraction, candidate, rel_tol=1e-9, abs_tol=1e-9)
        ),
        None,
    )
    if basis is None:
        raise AccountingError(
            f"atom_mass_fractions for {species!r} must sum to 1, 100, or 1000; "
            f"got {total_fraction:.12g}"
        )
    atoms: dict[str, float] = {}
    for symbol, fraction in parsed:
        normalized = fraction / basis
        atoms[symbol] = atoms.get(symbol, 0.0) + (
            normalized / ATOMIC_WEIGHTS_G_PER_MOL[symbol]
        )
    return atoms


def resolve_species_formula(
    species: str, registry: Mapping[str, Any] | None = None
) -> SpeciesFormula:
    """Resolve a species from a registry, falling back to parsing the species key."""

    if registry is not None and species in registry:
        try:
            return coerce_species_formula(species, registry[species])
        except AccountingError as exc:
            raise UnknownSpeciesError(f"invalid formula for species {species!r}") from exc

    try:
        return parse_formula(species, species=species)
    except AccountingError as exc:
        raise UnknownSpeciesError(f"unknown species {species!r}") from exc


def project_oxide_composition(
    values: Mapping[str, Any],
    *,
    source_basis: str,
) -> dict[str, float]:
    """Normalize an oxide composition from wt% or formula-unit moles.

    Formula resolution and mass conversion stay here so engine proxies,
    benchmarks, and redox output projection use the same oxide molar masses.
    """
    if source_basis not in {"wt_pct", "moles"}:
        raise ValueError("source_basis must be 'wt_pct' or 'moles'")
    projected: dict[str, float] = {}
    for raw_species, raw_amount in values.items():
        species = str(raw_species)
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError) as exc:
            raise AccountingError(f"{species} amount must be numeric") from exc
        if not math.isfinite(amount) or amount < 0.0:
            raise AccountingError(f"{species} amount must be finite and non-negative")
        if amount == 0.0:
            continue
        molar_mass = resolve_species_formula(species).molar_mass_g_per_mol()
        # Premise: wt% is proportional to mass, while formula-unit moles are
        # proportional to amount. Therefore n ∝ wt%/M and m ∝ n*M; the units
        # are respectively mol and g up to a common scale that normalization
        # cancels. A single positive species projects to fraction 1.
        projected[species] = (
            amount / molar_mass if source_basis == "wt_pct" else amount * molar_mass
        )
    total = sum(projected.values())
    if total <= 0.0:
        return {}
    return {species: amount / total for species, amount in sorted(projected.items())}


class _FormulaParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse(self) -> dict[str, float]:
        elements = self._parse_group(stop=None)
        if self.index != len(self.text):
            raise UnknownSpeciesError(
                f"unexpected formula token {self.text[self.index]!r} in {self.text!r}"
            )
        return elements

    def _parse_group(self, stop: str | None) -> dict[str, float]:
        elements: defaultdict[str, float] = defaultdict(float)
        while self.index < len(self.text):
            char = self.text[self.index]
            if stop is not None and char == stop:
                self.index += 1
                return dict(elements)
            if char in _CLOSE_TO_OPEN:
                raise UnknownSpeciesError(f"unmatched group close {char!r} in {self.text!r}")
            if char in _OPEN_TO_CLOSE:
                self.index += 1
                nested = self._parse_group(_OPEN_TO_CLOSE[char])
                multiplier = self._read_number(default=1.0)
                for element, count in nested.items():
                    elements[element] += count * multiplier
                continue
            if char.isupper():
                element = self._read_element()
                count = self._read_number(default=1.0, allow_leading_decimal=True)
                elements[element] += count
                continue
            raise UnknownSpeciesError(f"unexpected formula token {char!r} in {self.text!r}")

        if stop is not None:
            raise UnknownSpeciesError(f"missing group close {stop!r} in {self.text!r}")
        return dict(elements)

    def _read_element(self) -> str:
        start = self.index
        self.index += 1
        if self.index < len(self.text) and self.text[self.index].islower():
            self.index += 1
        return self.text[start : self.index]

    def _read_number(
        self, default: float, *, allow_leading_decimal: bool = False
    ) -> float:
        match = _NUMBER_RE.match(self.text, self.index)
        if match is None:
            return default
        if match.group(0).startswith(".") and not allow_leading_decimal:
            return default
        self.index = match.end()
        value = _formula_number(match.group(0))
        if value <= 0.0:
            raise AccountingError(f"formula multiplier must be positive in {self.text!r}")
        return value


def _load_formula_source(source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, (str, Path)):
        data = load_cached_safe_yaml(Path(source).read_text(encoding="utf-8")) or {}
    elif isinstance(source, Mapping):
        data = source
    else:
        raise AccountingError("species formula source must be a path or mapping")

    if not isinstance(data, Mapping):
        raise AccountingError("species formula source must resolve to a mapping")
    return data


def _extract_formula_entries(data: Mapping[str, Any]) -> dict[str, Any]:
    entries: Any = data
    for key in ("species", "species_formulas", "formulas"):
        nested = data.get(key)
        if isinstance(nested, (Mapping, Sequence)) and not isinstance(nested, (str, bytes)):
            entries = nested
            break

    if isinstance(entries, Mapping):
        return {str(species): spec for species, spec in entries.items()}
    if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
        normalized: dict[str, Any] = {}
        for entry in entries:
            if not isinstance(entry, Mapping) or "id" not in entry:
                raise AccountingError("list species entries must be mappings with id")
            normalized[str(entry["id"])] = entry
        return normalized
    raise AccountingError("species formula entries must be a mapping or list")


def _validate_case_aliases(entries: Mapping[str, Any]) -> None:
    seen: dict[str, tuple[str, str]] = {}
    for species, spec in entries.items():
        labels = [species]
        if isinstance(spec, Mapping):
            aliases = spec.get("aliases", ())
            if aliases is None:
                aliases = ()
            if isinstance(aliases, str):
                aliases = (aliases,)
            labels.extend(str(alias) for alias in aliases)
        for label in labels:
            previous = seen.get(label)
            if previous is not None and previous != (species, label):
                raise AccountingError(
                    f"species alias collision: {previous[1]!r} vs {label!r}"
                )
            seen[label] = (species, label)


def _declared_molar_mass(value: Mapping[str, Any]) -> float | None:
    for key in ("molar_mass_g_mol", "molar_mass_g_per_mol", "molar_mass"):
        if key in value and value[key] is not None:
            return float(value[key])
    return None


def _clean_formula_text(formula: str) -> str:
    text = re.sub(r"\s+", "", str(formula).strip())
    if text.startswith("$") and text.endswith("$"):
        text = text[1:-1]
    text = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", r"\1", text)
    text = (
        text.replace(r"\cdot", "·")
        .replace(r"\bullet", "·")
        .replace("⋅", "·")
        .replace("•", "·")
    )
    text = re.sub(
        r"_\{(\d+)\}|_(\d+)",
        lambda match: match.group(1) or match.group(2),
        text,
    )
    return fold_subscript_digits(text)


def _strip_one_leading_integer(part: str) -> str:
    end = 0
    while end < len(part) and part[end].isdigit():
        end += 1
    return part[end:]


def _distinct_integer_elements(text: str) -> int | None:
    """How many elements one integer-subscript formula names.

    None when the text is empty, does not parse, or a count is not a
    positive integer. A leading segment multiplier is not an element.
    """

    if not text or "." in text or "·" in text:
        return None
    try:
        multiplier, body = _leading_multiplier(text)
        if multiplier <= 0.0 or not multiplier.is_integer():
            return None
        elements = _FormulaParser(body).parse()
    except AccountingError:
        return None
    if not elements:
        return None
    for count in elements.values():
        if count <= 0.0 or not float(count).is_integer():
            return None
    return len(elements)


def _is_integer_compound_adduct(text: str) -> bool:
    """True when every ASCII dot separates multi-element integer formulas.

    Split on '.' and strip one leading integer multiplier from each part
    after the first. It is an adduct only if every part is an
    integer-subscript formula with at least two distinct elements;
    otherwise every dot is a decimal point. ``Al2O3.2SiO2.2H2O``,
    ``CuSO4.5H2O``, and ``Na2O.2SiO2`` split. ``Fe1.17Si3.13O8``,
    ``Mg1.8Fe1.2SiO4``, ``Na1.2Fe1.3Si3O8``, ``Ca1.5SiO3``,
    ``Mg1.5SiO4``, and ``Fe2.3O4`` stay one species because a part is a
    single element. ``FeO1.5`` stays one species because the part after
    the dot has no element. ``SiO1.5C0.5`` stays one species because it
    has a single-element part and an elementless part. An empty piece
    from a leading, trailing, or doubled dot is not an adduct.
    """

    if not text or "." not in text or "·" in text:
        return False
    parts = text.split(".")
    if any(part == "" for part in parts):
        return False
    for index, part in enumerate(parts):
        body = part if index == 0 else _strip_one_leading_integer(part)
        count = _distinct_integer_elements(body)
        if count is None or count < 2:
            return False
    return True


def _split_ascii_adducts(text: str) -> list[str]:
    if not text:
        return []
    if _is_integer_compound_adduct(text):
        return text.split(".")
    return [text]


def _split_formula_segments(formula: str) -> list[str]:
    """Split hydrate and adduct boundaries. Keep a decimal subscript intact.

    A middle dot (U+00B7) always separates segments: ``CuSO4·5H2O`` is
    five waters, and ``2Na2O·CaO·3SiO2`` is three oxides. An ASCII
    dot-string is an adduct only when splitting on '.' and stripping one
    leading integer from each part after the first leaves every part an
    integer-subscript formula of at least two distinct elements;
    otherwise every dot is a decimal point. ``Al2O3.2SiO2.2H2O``,
    ``CuSO4.5H2O``, and ``Na2O.2SiO2`` split. ``Fe1.17Si3.13O8``,
    ``Mg1.8Fe1.2SiO4``, ``Na1.2Fe1.3Si3O8``, ``Ca1.5SiO3``,
    ``Mg1.5SiO4``, ``Fe2.3O4``, ``FeO1.5``, and ``SiO1.5C0.5`` stay one
    species.
    """

    parts = formula.split("·")
    if any(not part for part in parts):
        raise UnknownSpeciesError(f"empty hydrate segment in {formula!r}")
    segments = [segment for part in parts for segment in _split_ascii_adducts(part)]
    if any(not segment for segment in segments):
        raise UnknownSpeciesError(f"empty adduct segment in {formula!r}")
    return segments


def _leading_multiplier(segment: str) -> tuple[float, str]:
    match = _NUMBER_RE.match(segment)
    if match is None or match.group(0).startswith("."):
        return 1.0, segment
    body = segment[match.end() :]
    if not body:
        raise UnknownSpeciesError(f"formula segment {segment!r} has no body")
    return _formula_number(match.group(0)), body


def _formula_number(token: str) -> float:
    if "/" not in token:
        return float(token)
    numerator, denominator = token.split("/", 1)
    denominator_value = int(denominator)
    if denominator_value == 0:
        raise AccountingError(f"formula denominator must be nonzero in {token!r}")
    return int(numerator) / denominator_value


def _close_enough(left: float, right: float, absolute: float, relative: float) -> bool:
    return abs(left - right) <= max(float(absolute), float(relative) * max(abs(left), abs(right), 1.0))
