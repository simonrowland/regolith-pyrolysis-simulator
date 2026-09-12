"""Content-derived species rail: feedstock element set plus occupancy tiers.

The rail is the element set of ``data/feedstocks.yaml`` composition keys. A
compilation record is in-rail iff every element in its formula is a rail
element. Out-of-rail records stay stored and are not scored.

Why ``janaf.feedstock_element_symbols`` (and not ``sgte_unary.feedstock_elements``)
---------------------------------------------------------------------------------
``janaf.feedstock_element_symbols`` walks every feedstock composition section
(``composition_wt_pct``, ``elemental_composition``, ``non_oxide_components``,
``bulk_additions``, ``structural_water``, ``trace_elements``,
``solar_wind_volatiles``) plus ``stage0_formula_inventory`` atoms and the
special keys ``CH4_NH3_HCN`` / ``CO_CO2``. That is the full declared element
set, including O/H/N/C and the solar-wind nobles.

``sgte_unary.feedstock_elements`` only walks ``composition_wt_pct`` and
``elemental_composition`` (positive amounts) plus stage-0 atoms, so it drops
Ar/He/Ne from solar-wind sections. One existing derivation is enough; this
module does not write a third walker.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from simulator.reference_data.janaf import (
    ELEMENT_SYMBOLS,
    FEEDSTOCKS_PATH,
    _NON_FORMULA_FEEDSTOCK_KEYS,
    feedstock_element_symbols,
    formula_composition,
    formula_elements,
    formula_normalised,
)

try:
    _YAML_LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _YAML_LOADER = yaml.SafeLoader

TIER_MAJOR = "major"
TIER_MINOR = "minor"
TIER_TRACE = "trace"
TIER_RANK = {TIER_MAJOR: 0, TIER_MINOR: 1, TIER_TRACE: 2}

# Occupancy histogram on data/feedstocks.yaml composition_wt_pct (29 feedstocks,
# counted 2026-09-12 from the live file, not a hand list of names):
#
#   18-27  oxide-forming majors (K=18 is the floor; O/Si/Fe/Al/Ca/Mg/Na/Ti above)
#   10-15  S, P, Mn, Ni, Cl
#   7      Co, Cr
#   6      REE / trace cluster (the ~6-feedstock band)
#   1-4    C, N, H
#   0      rail-only nobles (Ar, He, Ne) from non-wt_pct sections
#
# The two gaps that separate the three named bands are 18 vs 15 (major/minor)
# and 7 vs 6 (minor/trace). Co and Cr sit on the minor side of that 7-vs-6 gap.
# Thresholds are the gap edges; element names are not stored here.
MAJOR_MIN_FEEDSTOCKS = 18
MINOR_MIN_FEEDSTOCKS = 7

_LEADING_DOT_SUBSCRIPT_RE = re.compile(r"([A-Z][a-z]?)\.(\d+)")
_ANY_PHASE_SUFFIX_RE = re.compile(r"\([^)]*\)$")


@dataclass(frozen=True)
class SpeciesRail:
    """Element set plus per-element occupancy and tier."""

    elements: frozenset[str]
    occupancy: Mapping[str, int]
    n_feedstocks: int
    major_min_feedstocks: int = MAJOR_MIN_FEEDSTOCKS
    minor_min_feedstocks: int = MINOR_MIN_FEEDSTOCKS

    def element_tier(self, element: str) -> str | None:
        if element not in self.elements:
            return None
        count = int(self.occupancy.get(element, 0))
        if count >= self.major_min_feedstocks:
            return TIER_MAJOR
        if count >= self.minor_min_feedstocks:
            return TIER_MINOR
        return TIER_TRACE

    def formula_elements(self, formula: str) -> tuple[str, ...] | None:
        parsed = parse_formula_elements(formula)
        if parsed is None:
            return None
        return parsed

    def in_rail(self, formula: str) -> bool:
        parsed = parse_formula_elements(formula)
        if parsed is None or not parsed:
            return False
        return all(element in self.elements for element in parsed)

    def formula_tier(self, formula: str) -> str | None:
        """Lowest tier among the formula's elements, or None if out of rail.

        A formula containing any trace element is trace-tier.
        """

        parsed = parse_formula_elements(formula)
        if parsed is None or not parsed:
            return None
        if any(element not in self.elements for element in parsed):
            return None
        ranks = []
        for element in parsed:
            tier = self.element_tier(element)
            if tier is None:
                return None
            ranks.append(TIER_RANK[tier])
        return {0: TIER_MAJOR, 1: TIER_MINOR, 2: TIER_TRACE}[max(ranks)]


def parse_formula_elements(formula: str) -> tuple[str, ...] | None:
    """Element symbols in a compilation formula. Decimals kept; phase suffix stripped.

    ``Fe.947O`` (leading-dot subscript) is rewritten to ``Fe0.947O`` so the
    existing JANAF tokenizer can read it. The keyed formula string is not
    rewritten by callers; this helper is parse-only. Returns None when the
    string is not a composition.
    """

    raw = str(formula or "").strip()
    if not raw:
        return None
    raw = _ANY_PHASE_SUFFIX_RE.sub("", raw)
    raw = _LEADING_DOT_SUBSCRIPT_RE.sub(r"\g<1>0.\2", raw)
    if raw in ELEMENT_SYMBOLS:
        return (raw,)
    elements = formula_elements(raw)
    if elements:
        return elements
    composition = formula_composition(formula_normalised(raw))
    if composition:
        return tuple(element for element, _count in composition)
    return None


def parse_formula_composition(formula: str) -> tuple[tuple[str, float], ...] | None:
    """(element, count) pairs. Same decimal/suffix normalisation as the element parse."""

    raw = str(formula or "").strip()
    if not raw:
        return None
    raw = _ANY_PHASE_SUFFIX_RE.sub("", raw)
    raw = _LEADING_DOT_SUBSCRIPT_RE.sub(r"\g<1>0.\2", raw)
    if raw in ELEMENT_SYMBOLS:
        return ((raw, 1.0),)
    return formula_composition(raw)


def _positive_amount(value: object) -> bool:
    try:
        return float(value) > 0.0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _elements_in_composition_key(key: str) -> tuple[str, ...]:
    raw = str(key).strip()
    raw = re.sub(r"_(ppm|wt_pct|kg_per_tonne)$", "", raw)
    if raw in _NON_FORMULA_FEEDSTOCK_KEYS:
        if raw == "CH4_NH3_HCN":
            return ("C", "H", "N")
        if raw == "CO_CO2":
            return ("C", "O")
        return ()
    if raw in ELEMENT_SYMBOLS:
        return (raw,)
    parsed = parse_formula_elements(raw)
    return parsed or ()


def element_occupancy(
    feedstocks_path: Path | None = None,
) -> tuple[dict[str, int], int]:
    """How many feedstocks carry a ``composition_wt_pct`` species containing each element."""

    path = feedstocks_path or FEEDSTOCKS_PATH
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected a mapping")
    counts: Counter[str] = Counter()
    n_feedstocks = 0
    for row in payload.values():
        if not isinstance(row, Mapping):
            continue
        composition = row.get("composition_wt_pct")
        if not isinstance(composition, Mapping):
            continue
        n_feedstocks += 1
        present: set[str] = set()
        for key, amount in composition.items():
            if not _positive_amount(amount):
                continue
            present.update(_elements_in_composition_key(str(key)))
        counts.update(present)
    return dict(counts), n_feedstocks


def derive_species_rail(feedstocks_path: Path | None = None) -> SpeciesRail:
    path = feedstocks_path or FEEDSTOCKS_PATH
    elements = frozenset(feedstock_element_symbols(path))
    occupancy, n_feedstocks = element_occupancy(path)
    return SpeciesRail(
        elements=elements,
        occupancy=occupancy,
        n_feedstocks=n_feedstocks,
    )


def lowest_tier(tiers: Iterable[str]) -> str:
    ranked = [TIER_RANK[str(tier)] for tier in tiers]
    if not ranked:
        raise ValueError("lowest_tier of empty sequence")
    return {0: TIER_MAJOR, 1: TIER_MINOR, 2: TIER_TRACE}[max(ranked)]
