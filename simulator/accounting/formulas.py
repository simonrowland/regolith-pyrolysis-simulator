"""Species formula parsing and molar mass helpers."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError


ATOMIC_WEIGHTS_G_PER_MOL = MappingProxyType(
    {
        "H": 1.008,
        "He": 4.002602,
        "Li": 6.94,
        "Be": 9.0121831,
        "B": 10.81,
        "C": 12.011,
        "N": 14.007,
        "O": 16.0,
        "F": 18.998403163,
        "Ne": 20.1797,
        "Na": 22.98976928,
        "Mg": 24.305,
        "Al": 26.9815385,
        "Si": 28.08,
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
        "Fe": 55.84,
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

_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {v: k for k, v in _OPEN_TO_CLOSE.items()}
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_PHASE_SUFFIX_RE = re.compile(
    r"(?:\((?:s|l|g|aq|cr|liq|liquid|solid|gas|vapor)\)|"
    r"\[(?:s|l|g|aq|cr|liq|liquid|solid|gas|vapor)\])$",
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
    """Parse formula text and return a SpeciesFormula."""

    cleaned = _clean_formula_text(formula)
    if not cleaned:
        raise UnknownSpeciesError("formula is required")

    totals: defaultdict[str, float] = defaultdict(float)
    for segment in _split_formula_segments(cleaned):
        multiplier, body = _leading_multiplier(segment)
        parser = _FormulaParser(body)
        elements = parser.parse()
        for element, count in elements.items():
            totals[element] += count * multiplier

    species_id = species or name or str(formula).strip()
    return SpeciesFormula(species=species_id, elements=totals)


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
                count = self._read_number(default=1.0)
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

    def _read_number(self, default: float) -> float:
        match = _NUMBER_RE.match(self.text, self.index)
        if match is None:
            return default
        self.index = match.end()
        value = float(match.group(0))
        if value <= 0.0:
            raise AccountingError(f"formula multiplier must be positive in {self.text!r}")
        return value


def _load_formula_source(source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, (str, Path)):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise AccountingError("PyYAML is required to load formula YAML") from exc

        with Path(source).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
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
    previous = None
    while text and previous != text:
        previous = text
        text = _PHASE_SUFFIX_RE.sub("", text)
    return text


def _split_formula_segments(formula: str) -> list[str]:
    normalized = formula.replace("·", ".")
    segments = [segment for segment in normalized.split(".") if segment]
    if not segments:
        raise UnknownSpeciesError("formula is required")
    return segments


def _leading_multiplier(segment: str) -> tuple[float, str]:
    match = _NUMBER_RE.match(segment)
    if match is None:
        return 1.0, segment
    body = segment[match.end() :]
    if not body:
        raise UnknownSpeciesError(f"formula segment {segment!r} has no body")
    return float(match.group(0)), body


def _close_enough(left: float, right: float, absolute: float, relative: float) -> bool:
    return abs(left - right) <= max(float(absolute), float(relative) * max(abs(left), abs(right), 1.0))
