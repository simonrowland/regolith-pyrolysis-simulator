"""Source rail: JANAF / NASA Glenn / Burcat / Pankratz B677 → one P° and ΔfG.

No ranked selector. Selection is d-071 order (JANAF when it tabulates the
species, then NASA Glenn, Burcat, Pankratz B677). Every record is converted
to the 1 bar elemental reference used by Mode 2. Gases published at 1 atm
gain ``R T ln(1 bar / 1 atm)``; condensed VΔP is neglected. Native phase,
band, and source are recorded on every record. Ranking is only a two-source
G(T) comparison where two evaluable compilations overlap.

Gibbs convention (FOLD item 3): every returned record evaluates ΔfG(T)
relative to the elements in their reference states at T. JANAF and Pankratz
already tabulate that. NASA Glenn and Burcat publish absolute
G° = H°(T) − T·S°(T) on H298(elements) = 0; the rail converts with
ΔfG(T) = G°(species) − Σ ν_el·G°(element, same compilation, reference
state at T). One implementation; the runtime reaction rule stays
one compilation per reaction.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

from simulator.accounting.formulas import parse_formula
from simulator.reference_data import burcat, janaf, nasa_glenn
from simulator.reference_data.pankratz_1984_usbm_b677_loader import (
    COMPILATION_ROOT as PANKRATZ_ROOT,
    load_manifest as load_pankratz_manifest,
)
from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    Nasa7Segment,
    Nasa9Segment,
    NasaCeaError,
    NasaCeaPolynomial,
    ThermoState,
)
from simulator.vapour_rail.tabulated_gibbs import TabulatedThermo

STANDARD_PRESSURE_PA = 100_000.0
ATM_PRESSURE_PA = 101_325.0
# Thermochemical calorie (exactly 4.184 J). USBM B677 ΔGf is kcal/mol.
THERMOCHEMICAL_CALORIE_J = 4.184
KCAL_TO_J = THERMOCHEMICAL_CALORIE_J * 1000.0
JANAF_KJ_TO_J = 1000.0

# d-071 order. Not a scored ranker — first tabulated source wins.
SOURCE_ORDER: tuple[str, ...] = (
    "nist-janaf-4th",
    "nasa-glenn",
    "burcat",
    "pankratz-1984-usbm-b677",
)

# Common rail convention after conversion.
GIBBS_CONVENTION_FORMATION = "formation_gibbs"
# NASA Glenn / Burcat native: G° = H°(T) − T·S°(T), H298(elements) = 0.
GIBBS_CONVENTION_ABSOLUTE = "absolute_G"

# JANAF/CEA 298 K gas-phase elemental standards (diatomic). Coefficient in
# ΔfG is n_element / 2 because the reference species is E2(g).
# Diagnostic ΔfG uses this same map; Br2/I2 stay on the condensed map below.
DIATOMIC_GAS_REFERENCE: Mapping[str, str] = {
    "H": "H2",
    "N": "N2",
    "O": "O2",
    "F": "F2",
    "Cl": "Cl2",
}
_DIATOMIC_CONDENSED_REFERENCE: Mapping[str, str] = {
    "Br": "Br2",
    "I": "I2",
}
_NOBLE_GAS_ELEMENTS: frozenset[str] = frozenset(
    {"He", "Ne", "Ar", "Kr", "Xe", "Rn"}
)
# CEA solid allotropes that are not "cr".
_SOLID_ALLOTROPE_PHASES: frozenset[str] = frozenset(
    {"a", "b", "d", "gr", "I", "II", "III", "IV", "s", "S"}
)

_PHASE_TO_STANDARD_STATE: Mapping[str, str] = {
    "g": "gas",
    "gas": "gas",
    "0": "gas",
    "l": "condensed_liquid",
    "liq": "condensed_liquid",
    "liquid": "condensed_liquid",
    "cr": "condensed_solid",
    "s": "condensed_solid",
    "solid": "condensed_solid",
    "c": "condensed",
    "condensed": "condensed",
}


def canonical_standard_state(native_phase: str) -> str | None:
    token = str(native_phase).strip()
    if token.endswith(")") and "(" in token:
        token = token[token.rfind("(") + 1 : -1]
    key = token.lower().lstrip(".")
    return _PHASE_TO_STANDARD_STATE.get(key)


def compilation_standard_state(native_phase: str) -> str | None:
    """Map CEA/Burcat phase tokens, including solid allotropes, to a state."""
    mapped = canonical_standard_state(native_phase)
    if mapped is not None:
        return mapped
    token = str(native_phase).strip()
    if token == "L":
        return "condensed_liquid"
    if token in _SOLID_ALLOTROPE_PHASES:
        return "condensed_solid"
    return None


def _published_value(node: Any) -> float | None:
    if isinstance(node, Mapping):
        value = node.get("value")
    else:
        value = node
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def convert_gas_g_j_per_mol(
    g_j_per_mol: float,
    temperature_K: float,
    *,
    native_pressure_Pa: float,
    target_pressure_Pa: float = STANDARD_PRESSURE_PA,
) -> float:
    """Ideal-gas standard-state shift: G(P2) = G(P1) + R T ln(P2/P1)."""
    if native_pressure_Pa == target_pressure_Pa:
        return g_j_per_mol
    return g_j_per_mol + (
        R_J_PER_MOL_K
        * float(temperature_K)
        * math.log(target_pressure_Pa / native_pressure_Pa)
    )


@dataclass(frozen=True)
class SourceRailRecord:
    source_id: str
    record_id: str
    formula: str
    native_phase: str
    standard_state: str
    T_min_K: float
    T_max_K: float
    native_reference_pressure_Pa: float
    reference_pressure_Pa: float
    evaluator_family: str
    thermo: Any
    species_thermo: Mapping[str, Any]
    gibbs_convention: str = GIBBS_CONVENTION_FORMATION
    native_gibbs_convention: str = GIBBS_CONVENTION_FORMATION


class SourceCoverageGap(Exception):
    """Typed gap: a required (formula, phase) is absent from the rail."""

    def __init__(self, formula: str, phase: str, reason: str) -> None:
        self.formula = formula
        self.phase = phase
        self.reason = reason
        super().__init__(f"{formula} ({phase}): {reason}")


def formation_gibbs_from_absolute(
    g_abs_J_per_mol: float,
    composition: Mapping[str, float] | tuple[tuple[str, float], ...] | list[tuple[str, float]],
    element_reference_g_J_per_mol: Mapping[str, float],
    atoms_per_reference_species: Mapping[str, float],
) -> float:
    """ΔfG = G°(species) − Σ (n_el / n_std) · G°(reference species).

    ``element_reference_g_J_per_mol`` is G° of the reference species (O2, Si(cr), …),
    not per atom. ``atoms_per_reference_species`` is n_std for that species.
    Subtraction order follows ``composition``.
    """
    g = float(g_abs_J_per_mol)
    items = composition.items() if isinstance(composition, Mapping) else composition
    for element, count in items:
        n_std = float(atoms_per_reference_species[element])
        g -= (float(count) / n_std) * float(element_reference_g_J_per_mol[element])
    return g


def _reference_species(element: str) -> tuple[str, bool]:
    """Reference formula and whether the standard is gas-only."""
    if element in DIATOMIC_GAS_REFERENCE:
        return DIATOMIC_GAS_REFERENCE[element], True
    if element in _NOBLE_GAS_ELEMENTS:
        return element, True
    if element in _DIATOMIC_CONDENSED_REFERENCE:
        return _DIATOMIC_CONDENSED_REFERENCE[element], False
    return element, False


def _covering_records(
    records: tuple[SourceRailRecord, ...], temperature_K: float
) -> tuple[SourceRailRecord, ...]:
    return tuple(
        record
        for record in records
        if record.T_min_K <= temperature_K <= record.T_max_K
    )


def _element_reference_species_g(
    index: "_CompilationIndex", element: str, temperature_K: float
) -> tuple[float, float]:
    """G° of the reference species and the atom count of ``element`` in it."""
    formula, gas_only = _reference_species(element)
    composition = parse_formula(formula)
    atoms = float(composition.elements.get(element, 0.0))
    if atoms <= 0.0:
        raise SourceCoverageGap(
            formula, "reference", f"{formula} does not contain {element}"
        )
    chosen: SourceRailRecord | None = None
    if not gas_only:
        condensed: list[SourceRailRecord] = []
        for state in ("condensed_liquid", "condensed_solid", "condensed"):
            condensed.extend(index.native_records_for(formula, state))
        covering = _covering_records(tuple(condensed), temperature_K)
        if covering:
            liquids = [
                record
                for record in covering
                if record.standard_state == "condensed_liquid"
            ]
            chosen = liquids[0] if liquids else covering[0]
    if chosen is None:
        covering = _covering_records(
            index.native_records_for(formula, "gas"), temperature_K
        )
        if covering:
            chosen = covering[0]
    if chosen is None:
        raise SourceCoverageGap(
            formula,
            "reference",
            f"no {element} reference record covers {temperature_K} K",
        )
    return float(chosen.thermo.evaluate(temperature_K).g_J_per_mol), atoms


@dataclass
class FormationGibbsThermo:
    """Wrap absolute G° so ``evaluate`` returns ΔfG on the elemental reference."""

    native: Any
    formula: str
    index: Any

    @property
    def T_min_K(self) -> float:
        return float(self.native.T_min_K)

    @property
    def T_max_K(self) -> float:
        return float(self.native.T_max_K)

    def evaluate(self, T_K: float) -> ThermoState:
        T = float(T_K)
        native_state = self.native.evaluate(T)
        composition = parse_formula(self.formula)
        reference_g: dict[str, float] = {}
        atoms_per_reference: dict[str, float] = {}
        for element in composition.elements:
            g_species, n_std = _element_reference_species_g(self.index, element, T)
            reference_g[element] = g_species
            atoms_per_reference[element] = n_std
        g_j = formation_gibbs_from_absolute(
            float(native_state.g_J_per_mol),
            composition.elements,
            reference_g,
            atoms_per_reference,
        )
        return ThermoState(
            T_K=T,
            cp_over_R=math.nan,
            h_over_RT=math.nan,
            s_over_R=math.nan,
            g_over_RT=g_j / (R_J_PER_MOL_K * T),
        )


def _with_formation_convention(
    native: SourceRailRecord, index: "_CompilationIndex"
) -> SourceRailRecord:
    species_thermo = dict(native.species_thermo)
    species_thermo["native_gibbs_convention"] = index.native_gibbs_convention
    species_thermo["gibbs_convention"] = GIBBS_CONVENTION_FORMATION
    thermo: Any = native.thermo
    if index.native_gibbs_convention != GIBBS_CONVENTION_FORMATION:
        thermo = FormationGibbsThermo(
            native=native.thermo, formula=native.formula, index=index
        )
    return SourceRailRecord(
        source_id=native.source_id,
        record_id=native.record_id,
        formula=native.formula,
        native_phase=native.native_phase,
        standard_state=native.standard_state,
        T_min_K=native.T_min_K,
        T_max_K=native.T_max_K,
        native_reference_pressure_Pa=native.native_reference_pressure_Pa,
        reference_pressure_Pa=native.reference_pressure_Pa,
        evaluator_family=native.evaluator_family,
        thermo=thermo,
        species_thermo=species_thermo,
        gibbs_convention=GIBBS_CONVENTION_FORMATION,
        native_gibbs_convention=index.native_gibbs_convention,
    )


class _CompilationIndex:
    """Manifest (formula, standard_state) → on-disk locators, materialized on demand."""

    def __init__(
        self,
        locators: Mapping[tuple[str, str], tuple[Any, ...]],
        materialize: Callable[[Any], SourceRailRecord | None],
        *,
        native_gibbs_convention: str,
    ) -> None:
        self._locators = dict(locators)
        self._materialize = materialize
        self.native_gibbs_convention = native_gibbs_convention
        self._native_cache: dict[tuple[str, str], tuple[SourceRailRecord, ...]] = {}
        self._converted_cache: dict[tuple[str, str], tuple[SourceRailRecord, ...]] = {}

    def native_records_for(
        self, formula: str, standard_state: str
    ) -> tuple[SourceRailRecord, ...]:
        key = (formula, standard_state)
        if key in self._native_cache:
            return self._native_cache[key]
        out: list[SourceRailRecord] = []
        for locator in self._locators.get(key, ()):
            record = self._materialize(locator)
            if record is not None:
                out.append(record)
        self._native_cache[key] = tuple(out)
        return self._native_cache[key]

    def records_for(
        self, formula: str, standard_state: str
    ) -> tuple[SourceRailRecord, ...]:
        key = (formula, standard_state)
        if key in self._converted_cache:
            return self._converted_cache[key]
        converted = tuple(
            _with_formation_convention(record, self)
            for record in self.native_records_for(formula, standard_state)
        )
        self._converted_cache[key] = converted
        return converted


class SourceRail:
    """Indexed compilation records at 1 bar, keyed by (formula, standard_state)."""

    def __init__(self, indexes: Mapping[str, _CompilationIndex]) -> None:
        self._indexes = indexes
        self._merged: dict[tuple[str, str], tuple[SourceRailRecord, ...]] = {}

    def _index(self, source_id: str) -> _CompilationIndex | None:
        return self._indexes.get(source_id)

    def records_for(
        self, formula: str, standard_state: str
    ) -> tuple[SourceRailRecord, ...]:
        key = (formula, standard_state)
        if key in self._merged:
            return self._merged[key]
        found: list[SourceRailRecord] = []
        for source_id in SOURCE_ORDER:
            index = self._index(source_id)
            if index is None:
                continue
            found.extend(index.records_for(formula, standard_state))
        self._merged[key] = tuple(found)
        return self._merged[key]

    def select(
        self, formula: str, standard_state: str
    ) -> SourceRailRecord | None:
        group = self.records_for(formula, standard_state)
        return group[0] if group else None

    def require(
        self, formula: str, standard_state: str
    ) -> SourceRailRecord:
        record = self.select(formula, standard_state)
        if record is None:
            raise SourceCoverageGap(
                formula,
                standard_state,
                "no evaluable compilation record",
            )
        return record

    def overlapping_sources(
        self, formula: str, standard_state: str
    ) -> tuple[SourceRailRecord, ...]:
        return self.records_for(formula, standard_state)

    def select_common_source(
        self, participants: tuple[tuple[str, str], ...]
    ) -> tuple[str, dict[tuple[str, str], SourceRailRecord]] | None:
        """First d-071 source that tabulates every (formula, standard_state).

        One compilation per reaction. No scored ranker.
        """
        if not participants:
            return None
        for source_id in SOURCE_ORDER:
            index = self._index(source_id)
            if index is None:
                continue
            chosen: dict[tuple[str, str], SourceRailRecord] = {}
            complete = True
            for formula, standard_state in participants:
                group = index.records_for(formula, standard_state)
                if not group:
                    complete = False
                    break
                chosen[(formula, standard_state)] = group[0]
            if complete:
                return source_id, chosen
        return None

    def gases_for_element(self, element: str) -> tuple[SourceRailRecord, ...]:
        selected: list[SourceRailRecord] = []
        seen: set[str] = set()
        keys: set[tuple[str, str]] = set()
        for index in self._indexes.values():
            keys.update(index._locators)
        for formula, state in sorted(keys):
            if state != "gas" or formula in seen:
                continue
            try:
                parsed = parse_formula(formula)
            except Exception:
                continue
            if element not in parsed.elements:
                continue
            group = self.records_for(formula, state)
            if not group:
                continue
            seen.add(formula)
            selected.append(group[0])
        return tuple(selected)


def _janaf_points(document: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    table = document.get("table") or {}
    rows = table.get("values") or []
    points: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature") or {}
        gibbs = row.get("formation_gibbs_energy") or {}
        t_k = _published_value(temperature)
        g_kj = _published_value(gibbs)
        if t_k is None or g_kj is None or t_k <= 0.0:
            continue
        points.append((t_k, g_kj * JANAF_KJ_TO_J))
    points.sort(key=lambda item: item[0])
    # Drop duplicate T, keep first finite printed row.
    unique: list[tuple[float, float]] = []
    for t_k, g_j in points:
        if unique and t_k == unique[-1][0]:
            continue
        unique.append((t_k, g_j))
    return tuple(unique)


def _janaf_skip_reason(index_entry: Mapping[str, Any]) -> str | None:
    state = str(index_entry.get("state") or index_entry.get("phase") or "")
    if state in {"ref", "cr,l", "l,g"}:
        return "combined-or-reference table"
    charge = index_entry.get("charge")
    if charge not in (None, 0, 0.0, "0"):
        return "ionized"
    title = str(index_entry.get("name") or "")
    if "+" in title or title.endswith("-"):
        return "ionized"
    return None


def _janaf_record_from_path(path: Path) -> SourceRailRecord | None:
    document = janaf.load_table_document(path)
    table = document.get("table") or {}
    index_entry = table.get("index_entry") or {}
    if _janaf_skip_reason(index_entry):
        return None
    formula = str(
        index_entry.get("formula_normalised")
        or index_entry.get("formula")
        or ""
    )
    native_phase = str(index_entry.get("state") or "")
    standard_state = canonical_standard_state(native_phase)
    if not formula or standard_state is None:
        return None
    points = _janaf_points(document)
    if len(points) < 2:
        return None
    table_id = str(table.get("table_id") or path.stem)
    thermo = TabulatedThermo(
        name=f"janaf:{table_id}",
        standard_state=standard_state,  # type: ignore[arg-type]
        formation_gibbs_J_per_mol=points,
        formula=formula,
        citation=str((document.get("source") or {}).get("citation") or ""),
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        source_id="nist-janaf-4th",
        record_id=table_id,
        native_phase=native_phase,
        native_reference_pressure_Pa=STANDARD_PRESSURE_PA,
    )
    species_thermo = {
        "evaluator_family": "tabulated_janaf",
        "standard_state": standard_state,
        "reference_pressure_Pa": STANDARD_PRESSURE_PA,
        "formation_gibbs_points": [
            {"T_K": t_k, "delta_f_G_J_per_mol": g_j} for t_k, g_j in points
        ],
        "source_id": "nist-janaf-4th",
        "record_id": table_id,
        "native_phase": native_phase,
        "citation": thermo.citation,
    }
    return SourceRailRecord(
        source_id="nist-janaf-4th",
        record_id=table_id,
        formula=formula,
        native_phase=native_phase,
        standard_state=standard_state,
        T_min_K=thermo.T_min_K,
        T_max_K=thermo.T_max_K,
        native_reference_pressure_Pa=STANDARD_PRESSURE_PA,
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        evaluator_family="tabulated_janaf",
        thermo=thermo,
        species_thermo=species_thermo,
    )


def _janaf_index() -> _CompilationIndex:
    locators: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for entry in janaf.load_manifest().get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        if _janaf_skip_reason(entry):
            continue
        formula = str(entry.get("formula_normalised") or entry.get("formula") or "")
        standard_state = canonical_standard_state(str(entry.get("phase") or ""))
        table_id = str(entry.get("table_id") or "")
        if not formula or standard_state is None or not table_id:
            continue
        path = janaf.TABLES_DIR / f"{table_id}.yaml"
        locators[(formula, standard_state)].append(path)
    return _CompilationIndex(
        {key: tuple(paths) for key, paths in locators.items()},
        _janaf_record_from_path,
        native_gibbs_convention=GIBBS_CONVENTION_FORMATION,
    )


def _nasa_standard_state(phase: str) -> str | None:
    return compilation_standard_state(phase)


def _nasa9_from_document(document: Mapping[str, Any]) -> NasaCeaPolynomial | None:
    intervals = document.get("intervals") or []
    if not intervals:
        return None
    segments: list[Nasa9Segment] = []
    try:
        for interval in intervals:
            t_min = _published_value(interval.get("T_min_K"))
            t_max = _published_value(interval.get("T_max_K"))
            b1 = _published_value(interval.get("b1"))
            b2 = _published_value(interval.get("b2"))
            coeffs_raw = interval.get("a_coefficients") or []
            coeffs = [_published_value(item) for item in coeffs_raw[:7]]
            if (
                t_min is None
                or t_max is None
                or b1 is None
                or b2 is None
                or any(c is None for c in coeffs)
                or len(coeffs) != 7
            ):
                return None
            segments.append(
                Nasa9Segment(
                    t_min,
                    t_max,
                    tuple(float(c) for c in coeffs),  # type: ignore[arg-type]
                    float(b1),
                    float(b2),
                )
            )
    except NasaCeaError:
        return None
    if not segments:
        return None
    standard_state = _nasa_standard_state(str(document.get("phase") or ""))
    if standard_state is None:
        return None
    try:
        return NasaCeaPolynomial(
            name=str(document.get("name_as_published") or document.get("record_id")),
            family="nasa_cea_9",
            standard_state=standard_state,  # type: ignore[arg-type]
            segments=tuple(segments),
            formula=str(document.get("formula") or ""),
            delta_f_H_298_15_J_per_mol=_published_value(
                document.get("delta_f_H_298_15")
            ),
            citation=str(document.get("citation_as_published") or ""),
            reference_pressure_Pa=STANDARD_PRESSURE_PA,
        )
    except Exception:
        return None


def _species_thermo_from_nasa9(
    poly: NasaCeaPolynomial, document: Mapping[str, Any]
) -> dict[str, Any]:
    segments = []
    for seg in poly.segments:
        assert isinstance(seg, Nasa9Segment)
        segments.append(
            {
                "T_min_K": seg.T_min_K,
                "T_max_K": seg.T_max_K,
                "a_coefficients": list(seg.coefficients),
                "b1": seg.b1,
                "b2": seg.b2,
            }
        )
    return {
        "evaluator_family": "nasa_cea_9",
        "standard_state": poly.standard_state,
        "reference_pressure_Pa": STANDARD_PRESSURE_PA,
        "formula": poly.formula,
        "delta_f_H_298_15_J_per_mol": poly.delta_f_H_298_15_J_per_mol,
        "segments": segments,
        "source_id": "nasa-glenn",
        "record_id": str(document.get("record_id") or ""),
        "native_phase": str(document.get("phase") or ""),
        "citation": poly.citation,
    }


def _nasa_record_from_path(path: Path) -> SourceRailRecord | None:
    document = nasa_glenn.load_record_document(path)
    poly = _nasa9_from_document(document)
    if poly is None:
        return None
    formula = str(document.get("formula") or "")
    if not formula:
        return None
    native_phase = str(document.get("phase") or "")
    record_id = str(document.get("record_id") or "")
    return SourceRailRecord(
        source_id="nasa-glenn",
        record_id=record_id,
        formula=formula,
        native_phase=native_phase,
        standard_state=poly.standard_state,
        T_min_K=poly.T_min_K,
        T_max_K=poly.T_max_K,
        native_reference_pressure_Pa=STANDARD_PRESSURE_PA,
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        evaluator_family="nasa_cea_9",
        thermo=poly,
        species_thermo=_species_thermo_from_nasa9(poly, document),
    )


def _nasa_glenn_index() -> _CompilationIndex:
    locators: dict[tuple[str, str], list[Path]] = defaultdict(list)
    root = Path(__file__).resolve().parents[2]
    for entry in nasa_glenn.load_manifest().get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        formula = str(entry.get("formula") or "")
        standard_state = _nasa_standard_state(str(entry.get("phase") or ""))
        rel = str(entry.get("path") or "")
        if not formula or standard_state is None or not rel:
            continue
        locators[(formula, standard_state)].append(root / rel)
    return _CompilationIndex(
        {key: tuple(paths) for key, paths in locators.items()},
        _nasa_record_from_path,
        native_gibbs_convention=GIBBS_CONVENTION_ABSOLUTE,
    )


def _burcat_join_K(intervals: list[Mapping[str, Any]]) -> float:
    for interval in intervals:
        tag = str(interval.get("xml_range_tag") or "")
        if "1000" in tag:
            return 1000.0
    return 1000.0


def _nasa7_from_burcat(document: Mapping[str, Any]) -> NasaCeaPolynomial | None:
    if document.get("record_kind") != "nasa7_polynomial":
        return None
    intervals = document.get("intervals") or []
    t_min = _published_value(document.get("T_min_K"))
    t_max = _published_value(document.get("T_max_K"))
    if t_min is None or t_max is None or len(intervals) != 2:
        return None
    join = _burcat_join_K(intervals)
    by_tag: dict[str, Mapping[str, Any]] = {}
    for interval in intervals:
        tag = str(interval.get("range_as_published") or "")
        by_tag[tag] = interval
    low = by_tag.get("low_T_second")
    high = by_tag.get("high_T_first")
    if low is None or high is None:
        return None

    def coeffs(interval: Mapping[str, Any]) -> tuple[float, ...] | None:
        values = [_published_value(item) for item in interval.get("a_coefficients") or []]
        if len(values) != 7 or any(v is None for v in values):
            return None
        return tuple(float(v) for v in values)  # type: ignore[misc]

    low_c = coeffs(low)
    high_c = coeffs(high)
    if low_c is None or high_c is None:
        return None
    standard_state = compilation_standard_state(str(document.get("phase") or ""))
    if standard_state is None:
        return None
    try:
        segments = (
            Nasa7Segment(t_min, join, low_c),  # type: ignore[arg-type]
            Nasa7Segment(join, t_max, high_c),  # type: ignore[arg-type]
        )
        return NasaCeaPolynomial(
            name=str(document.get("name_as_published") or document.get("record_id")),
            family="nasa_cea_7",
            standard_state=standard_state,  # type: ignore[arg-type]
            segments=segments,
            formula=str(document.get("formula") or ""),
            citation=str(document.get("cas_as_published") or ""),
            reference_pressure_Pa=STANDARD_PRESSURE_PA,
        )
    except Exception:
        return None


def _species_thermo_from_nasa7(
    poly: NasaCeaPolynomial, document: Mapping[str, Any]
) -> dict[str, Any]:
    segments = []
    for seg in poly.segments:
        assert isinstance(seg, Nasa7Segment)
        segments.append(
            {
                "T_min_K": seg.T_min_K,
                "T_max_K": seg.T_max_K,
                "coefficients": list(seg.coefficients),
            }
        )
    return {
        "evaluator_family": "nasa_cea_7",
        "standard_state": poly.standard_state,
        "reference_pressure_Pa": STANDARD_PRESSURE_PA,
        "formula": poly.formula,
        "segments": segments,
        "source_id": "burcat",
        "record_id": str(document.get("record_id") or ""),
        "native_phase": str(document.get("phase") or ""),
        "citation": poly.citation,
    }


def _burcat_record_from_path(path: Path) -> SourceRailRecord | None:
    document = burcat.load_record_document(path)
    poly = _nasa7_from_burcat(document)
    if poly is None:
        return None
    formula = str(document.get("formula") or "")
    if not formula:
        return None
    return SourceRailRecord(
        source_id="burcat",
        record_id=str(document.get("record_id") or ""),
        formula=formula,
        native_phase=str(document.get("phase") or ""),
        standard_state=poly.standard_state,
        T_min_K=poly.T_min_K,
        T_max_K=poly.T_max_K,
        native_reference_pressure_Pa=STANDARD_PRESSURE_PA,
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        evaluator_family="nasa_cea_7",
        thermo=poly,
        species_thermo=_species_thermo_from_nasa7(poly, document),
    )


def _burcat_index() -> _CompilationIndex:
    locators: dict[tuple[str, str], list[Path]] = defaultdict(list)
    root = Path(__file__).resolve().parents[2]
    for entry in burcat.load_manifest().get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("record_kind") not in (None, "nasa7_polynomial"):
            continue
        formula = str(entry.get("formula") or "")
        standard_state = compilation_standard_state(str(entry.get("phase") or ""))
        rel = str(entry.get("path") or "")
        if not formula or standard_state is None or not rel:
            continue
        locators[(formula, standard_state)].append(root / rel)
    return _CompilationIndex(
        {key: tuple(paths) for key, paths in locators.items()},
        _burcat_record_from_path,
        native_gibbs_convention=GIBBS_CONVENTION_ABSOLUTE,
    )


def _pankratz_phase(record: Mapping[str, Any]) -> str | None:
    token = record.get("phase_token") or {}
    raw = str(token.get("value") or record.get("phase_as_published") or "")
    return canonical_standard_state(raw)


def _pankratz_points(record: Mapping[str, Any], *, is_gas: bool) -> tuple[tuple[float, float], ...]:
    rows = record.get("rows") or []
    points: list[tuple[float, float]] = []
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) < 6:
            continue
        t_k = _published_value(cells[0])
        g_kcal = _published_value(cells[5])
        if t_k is None or g_kcal is None or t_k <= 0.0:
            continue
        g_j = g_kcal * KCAL_TO_J
        if is_gas:
            g_j = convert_gas_g_j_per_mol(
                g_j,
                t_k,
                native_pressure_Pa=ATM_PRESSURE_PA,
                target_pressure_Pa=STANDARD_PRESSURE_PA,
            )
        points.append((t_k, g_j))
    points.sort(key=lambda item: item[0])
    unique: list[tuple[float, float]] = []
    for t_k, g_j in points:
        if unique and t_k == unique[-1][0]:
            continue
        unique.append((t_k, g_j))
    return tuple(unique)


def _pankratz_record_from_path(path: Path) -> SourceRailRecord | None:
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("transcription_status") == "untranscribed":
        return None
    formula = str(record.get("formula_as_published") or record.get("formula") or "")
    standard_state = _pankratz_phase(record)
    if not formula or standard_state is None:
        return None
    points = _pankratz_points(record, is_gas=(standard_state == "gas"))
    if len(points) < 2:
        return None
    record_id = str(record.get("record_id") or "")
    native_phase = str(
        (record.get("phase_token") or {}).get("value")
        or record.get("phase_as_published")
        or record.get("phase")
        or ""
    )
    thermo = TabulatedThermo(
        name=f"pankratz:{record_id}",
        standard_state=standard_state,  # type: ignore[arg-type]
        formation_gibbs_J_per_mol=points,
        formula=formula,
        citation="Pankratz, Stuve, Gokcen, USBM Bulletin 677 (1984)",
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        source_id="pankratz-1984-usbm-b677",
        record_id=record_id,
        native_phase=native_phase,
        native_reference_pressure_Pa=(
            ATM_PRESSURE_PA if standard_state == "gas" else STANDARD_PRESSURE_PA
        ),
    )
    species_thermo = {
        "evaluator_family": "tabulated_janaf",
        "standard_state": standard_state,
        "reference_pressure_Pa": STANDARD_PRESSURE_PA,
        "formation_gibbs_points": [
            {"T_K": t_k, "delta_f_G_J_per_mol": g_j} for t_k, g_j in points
        ],
        "source_id": "pankratz-1984-usbm-b677",
        "record_id": record_id,
        "native_phase": native_phase,
        "native_reference_pressure_Pa": thermo.native_reference_pressure_Pa,
        "citation": thermo.citation,
    }
    return SourceRailRecord(
        source_id="pankratz-1984-usbm-b677",
        record_id=record_id,
        formula=formula,
        native_phase=native_phase,
        standard_state=standard_state,
        T_min_K=thermo.T_min_K,
        T_max_K=thermo.T_max_K,
        native_reference_pressure_Pa=thermo.native_reference_pressure_Pa or ATM_PRESSURE_PA,
        reference_pressure_Pa=STANDARD_PRESSURE_PA,
        evaluator_family="tabulated_janaf",
        thermo=thermo,
        species_thermo=species_thermo,
    )


def _pankratz_index() -> _CompilationIndex:
    locators: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for entry in load_pankratz_manifest().get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("transcription_status") == "untranscribed":
            continue
        formula = str(entry.get("formula") or entry.get("formula_as_published") or "")
        standard_state = canonical_standard_state(str(entry.get("phase") or ""))
        rel = str(entry.get("path") or "")
        if not formula or standard_state is None or not rel:
            continue
        locators[(formula, standard_state)].append(PANKRATZ_ROOT / rel)
    return _CompilationIndex(
        {key: tuple(paths) for key, paths in locators.items()},
        _pankratz_record_from_path,
        native_gibbs_convention=GIBBS_CONVENTION_FORMATION,
    )


@lru_cache(maxsize=1)
def load_source_rail() -> SourceRail:
    return SourceRail(
        {
            "nist-janaf-4th": _janaf_index(),
            "nasa-glenn": _nasa_glenn_index(),
            "burcat": _burcat_index(),
            "pankratz-1984-usbm-b677": _pankratz_index(),
        }
    )


def compare_g_over_overlap(
    left: SourceRailRecord,
    right: SourceRailRecord,
    temperatures_K: tuple[float, ...] = (1400.0, 1600.0, 1800.0),
) -> tuple[dict[str, Any], ...]:
    """Two-source G(T) sample on the overlapping band. Disagreements stay visible."""
    low = max(left.T_min_K, right.T_min_K)
    high = min(left.T_max_K, right.T_max_K)
    rows: list[dict[str, Any]] = []
    if high <= low:
        return tuple(rows)
    for temperature_K in temperatures_K:
        if temperature_K < low or temperature_K > high:
            continue
        g_left = float(left.thermo.evaluate(temperature_K).g_J_per_mol)
        g_right = float(right.thermo.evaluate(temperature_K).g_J_per_mol)
        rows.append(
            {
                "T_K": temperature_K,
                "left_source": left.source_id,
                "right_source": right.source_id,
                "left_g_J_per_mol": g_left,
                "right_g_J_per_mol": g_right,
                "delta_g_J_per_mol": g_left - g_right,
                "left_gibbs_convention": left.gibbs_convention,
                "right_gibbs_convention": right.gibbs_convention,
            }
        )
    return tuple(rows)
