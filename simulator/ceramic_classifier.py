from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.fe_redox import (
    floor_vacuum_pressure_bar,
    kress91_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)
from simulator.state import MOLAR_MASS


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_CERAMIC_TYPES_PATH = DATA_DIR / "ceramic_types.yaml"
DEFAULT_GLASS_TYPES_PATH = DATA_DIR / "glass_types.yaml"
DEFAULT_ANALYTICAL_TOLERANCE_WT_PCT = 0.5


@dataclass(frozen=True)
class CeramicServiceTemperature:
    value_C: float | None
    kind: str
    usable_service_C: float | None
    citations: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class CeramicMatch:
    ceramic_id: str
    label: str
    composition_kind: str
    service_temp: CeramicServiceTemperature
    liner_suitability: dict[str, Any]
    parent_id: str | None
    match_level: str
    hierarchy: tuple[str, ...]
    datasheet: dict[str, Any]


@dataclass(frozen=True)
class CeramicClassification:
    match: CeramicMatch | None
    tolerance_wt_pct: float
    status: str
    reason: str


@dataclass(frozen=True)
class GlassMatch:
    family_id: str
    label: str
    parent_id: str | None
    match_level: str
    hierarchy: tuple[str, ...]
    composition_kind: str
    use_grade: tuple[str, ...]
    datasheet: dict[str, Any]


@dataclass(frozen=True)
class GlassClassification:
    match: GlassMatch | None
    tolerance_wt_pct: float
    status: str
    reason: str
    clarity_grade: str
    colour_estimate: str
    use_grade_optical: tuple[str, ...]
    total_fe2o3_wt_pct: float
    fe2_fraction: float | None
    redox_source: str
    confidence: str
    model_citations: tuple[str, ...]


def load_ceramic_types(path: Path | str = DEFAULT_CERAMIC_TYPES_PATH) -> dict[str, Any]:
    return _load_types(path, "ceramics", "ceramic")


def load_glass_types(path: Path | str = DEFAULT_GLASS_TYPES_PATH) -> dict[str, Any]:
    return _load_types(path, "glass_types", "glass")


def _load_types(path: Path | str, key: str, label: str) -> dict[str, Any]:
    with Path(path).open() as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        raise ValueError(f"{label} type data is malformed: {path}")
    return data


def classify_ceramic_rump(
    composition_wt_pct: Mapping[str, float],
    *,
    tolerance_wt_pct: float = DEFAULT_ANALYTICAL_TOLERANCE_WT_PCT,
    data_path: Path | str = DEFAULT_CERAMIC_TYPES_PATH,
) -> CeramicClassification:
    _validate_tolerance(tolerance_wt_pct)
    data = load_ceramic_types(data_path)
    ignored = set(data.get("ignored_identity_oxides") or ())
    composition = _normalized_composition(composition_wt_pct, ignored)
    status, selected, reason = _hierarchical_match(
        data["ceramics"], composition, tolerance_wt_pct
    )
    match = None
    if selected is not None:
        material_id, entry = selected
        match = _ceramic_match(material_id, entry, data["ceramics"])
    return CeramicClassification(
        match=match,
        tolerance_wt_pct=float(tolerance_wt_pct),
        status=status,
        reason=reason.replace("material", "ceramic"),
    )


def classify_industrial_glass(
    composition_wt_pct: Mapping[str, float],
    *,
    pO2_mbar: float | None = None,
    temperature_C: float | None = None,
    pressure_mbar: float | None = None,
    fe2_fraction: float | None = None,
    tolerance_wt_pct: float = DEFAULT_ANALYTICAL_TOLERANCE_WT_PCT,
    data_path: Path | str = DEFAULT_GLASS_TYPES_PATH,
) -> GlassClassification:
    _validate_tolerance(tolerance_wt_pct)
    composition = _positive_composition(composition_wt_pct)
    data = load_glass_types(data_path)
    status, selected, reason = _hierarchical_match(
        data["glass_types"], composition, tolerance_wt_pct
    )
    match = None
    if selected is not None:
        material_id, entry = selected
        match = _glass_match(material_id, entry, data["glass_types"])

    clarity_model = data["clarity_model"]
    total_fe = _total_fe2o3_equivalent(composition, clarity_model)
    resolved_fe2, redox_source = _resolve_fe2_fraction(
        composition,
        pO2_mbar=pO2_mbar,
        temperature_C=temperature_C,
        pressure_mbar=pressure_mbar,
        explicit=fe2_fraction,
    )
    clarity = _band_value(
        total_fe, clarity_model["clarity_bands"], "grade"
    )
    if match is not None and match.family_id == "basalt_high_fe_glass":
        if _clarity_rank(clarity) < _clarity_rank("strongly_coloured"):
            clarity = "strongly_coloured"
    colour = _glass_colour(total_fe, resolved_fe2, clarity_model)
    optical_grades = _gated_use_grades(match, clarity)
    return GlassClassification(
        match=match,
        tolerance_wt_pct=float(tolerance_wt_pct),
        status=status,
        reason=reason.replace("material", "glass family"),
        clarity_grade=clarity,
        colour_estimate=colour,
        use_grade_optical=optical_grades,
        total_fe2o3_wt_pct=total_fe,
        fe2_fraction=resolved_fe2,
        redox_source=redox_source,
        confidence=str(clarity_model.get("confidence", "estimate")),
        model_citations=tuple(clarity_model.get("citations") or ()),
    )


def _validate_tolerance(value: float) -> None:
    if value < 0:
        raise ValueError("tolerance_wt_pct must be non-negative")


def _positive_composition(values: Mapping[str, float]) -> dict[str, float]:
    result = {}
    for oxide, value in values.items():
        amount = float(value)
        if amount > 0.0:
            result[str(oxide)] = amount
    return result


def _normalized_composition(
    values: Mapping[str, float], ignored: set[str]
) -> dict[str, float]:
    composition = {
        oxide: value
        for oxide, value in _positive_composition(values).items()
        if oxide not in ignored
    }
    total = sum(composition.values())
    if total <= 0.0:
        return {}
    return {oxide: value / total * 100.0 for oxide, value in composition.items()}


def _hierarchical_match(
    entries: Mapping[str, dict[str, Any]],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> tuple[str, tuple[str, dict[str, Any]] | None, str]:
    parent_hits = {
        material_id: entry
        for material_id, entry in entries.items()
        if entry.get("parent") is None
        and _matches_composition(entry["composition"], composition, tolerance_wt_pct)
    }
    subtype_hits = [
        (material_id, entry)
        for material_id, entry in entries.items()
        if entry.get("parent") is not None
        and _matches_composition(entry["composition"], composition, tolerance_wt_pct)
    ]
    if subtype_hits:
        ranked_subtypes = sorted(
            subtype_hits,
            key=lambda item: _subtype_specificity(
                item[1], composition, tolerance_wt_pct
            ),
            reverse=True,
        )
        top_specificity = _subtype_specificity(
            ranked_subtypes[0][1], composition, tolerance_wt_pct
        )
        most_specific = [
            item
            for item in ranked_subtypes
            if _subtype_specificity(
                item[1], composition, tolerance_wt_pct
            ) == top_specificity
        ]
    else:
        most_specific = []
    if len(most_specific) == 1:
        return (
            "match",
            most_specific[0],
            "composition matched the most-specific source-supported subtype predicate",
        )
    if len(most_specific) > 1:
        parent_ids = {entry.get("parent") for _material_id, entry in most_specific}
        if len(parent_ids) == 1:
            parent_id = next(iter(parent_ids))
            if parent_id in parent_hits:
                return (
                    "match",
                    (str(parent_id), parent_hits[str(parent_id)]),
                    "equally specific sibling subtype predicates tied; fell back to parent predicate",
                )
        ids = ", ".join(material_id for material_id, _entry in most_specific)
        return "ambiguous", None, f"ambiguous material subtype matches: {ids}"
    if len(parent_hits) == 1:
        selected = next(iter(parent_hits.items()))
        return (
            "match",
            selected,
            "composition matched parent predicate; no subtype predicate matched",
        )
    if len(parent_hits) > 1:
        ranked = sorted(
            parent_hits.items(),
            key=lambda item: int(item[1].get("priority", 0)),
            reverse=True,
        )
        top_priority = int(ranked[0][1].get("priority", 0))
        next_priority = int(ranked[1][1].get("priority", 0))
        if top_priority > next_priority:
            return (
                "match",
                ranked[0],
                "composition matched the highest-priority parent predicate",
            )
        return (
            "ambiguous",
            None,
            f"ambiguous material parent matches: {', '.join(parent_hits)}",
        )
    return "no-match", None, "composition outside source-supported material predicates"


def _subtype_specificity(
    entry: Mapping[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> tuple[int, int]:
    spec = entry["composition"]
    anchor = spec.get("point_anchor_wt_pct")
    matches_alternate_anchor = bool(anchor) and _matches_point_anchor(
        {
            "wt_pct": anchor,
            "allow_other_oxides": False,
        },
        composition,
        tolerance_wt_pct,
    )
    kind_rank = (
        1
        if spec.get("kind") == "point-anchor" or matches_alternate_anchor
        else 0
    )
    return kind_rank, int(entry.get("priority", 0))


def _matches_composition(
    spec: Mapping[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    kind = spec.get("kind")
    matched = False
    if kind == "point-anchor":
        matched = _matches_point_anchor(spec, composition, tolerance_wt_pct)
    elif kind == "window":
        matched = _matches_window(spec, composition, tolerance_wt_pct)
    else:
        raise ValueError(f"unknown composition kind: {kind}")
    alternate_anchor = spec.get("point_anchor_wt_pct")
    if not matched and alternate_anchor:
        matched = _matches_point_anchor(
            {
                "wt_pct": alternate_anchor,
                "allow_other_oxides": False,
            },
            composition,
            tolerance_wt_pct,
        )
    if not matched:
        return False
    return _matches_constraints(
        spec.get("constraints") or {}, composition, tolerance_wt_pct
    )


def _matches_point_anchor(
    spec: Mapping[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    target = spec.get("wt_pct") or {}
    for oxide, expected in target.items():
        if abs(composition.get(oxide, 0.0) - float(expected)) > tolerance_wt_pct:
            return False
    return _other_oxides_allowed(spec, composition, set(target), tolerance_wt_pct)


def _matches_window(
    spec: Mapping[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    window = spec.get("wt_pct_window") or {}
    for oxide, bounds in window.items():
        lower, upper = bounds
        actual = composition.get(oxide, 0.0)
        if actual < float(lower) - tolerance_wt_pct:
            return False
        if actual > float(upper) + tolerance_wt_pct:
            return False
    defining = set(spec.get("defining_oxides") or window)
    return _other_oxides_allowed(spec, composition, defining, tolerance_wt_pct)


def _other_oxides_allowed(
    spec: Mapping[str, Any],
    composition: Mapping[str, float],
    defining: set[str],
    tolerance_wt_pct: float,
) -> bool:
    if spec.get("allow_other_oxides", False):
        return True
    return all(
        oxide in defining or abs(float(actual)) <= tolerance_wt_pct
        for oxide, actual in composition.items()
    )


def _matches_constraints(
    constraints: Mapping[str, Any],
    composition: Mapping[str, float],
    tolerance: float,
) -> bool:
    for row in constraints.get("sum_min") or ():
        if _oxide_sum(composition, row["oxides"]) < float(row["value"]) - tolerance:
            return False
    for row in constraints.get("sum_max") or ():
        if _oxide_sum(composition, row["oxides"]) > float(row["value"]) + tolerance:
            return False
    for row in constraints.get("sum_window") or ():
        value = _oxide_sum(composition, row["oxides"])
        lower, upper = row["range"]
        if value < float(lower) - tolerance or value > float(upper) + tolerance:
            return False
    for row in constraints.get("all_min") or ():
        if any(composition.get(oxide, 0.0) < float(row["value"]) - tolerance for oxide in row["oxides"]):
            return False
    for row in constraints.get("any_min") or ():
        if not any(composition.get(oxide, 0.0) >= float(row["value"]) - tolerance for oxide in row["oxides"]):
            return False
    for row in constraints.get("ratio_window") or ():
        denominator = _oxide_sum(composition, row["denominator"])
        if denominator <= 0.0:
            return False
        ratio = composition.get(row["numerator"], 0.0) / denominator
        lower, upper = row["range"]
        if ratio < float(lower) or ratio > float(upper):
            return False
    minimum_fe = constraints.get("fe2o3_equivalent_min")
    if minimum_fe is not None:
        total_fe = composition.get("Fe2O3", 0.0) + 1.1113 * composition.get("FeO", 0.0)
        if total_fe < float(minimum_fe) - tolerance:
            return False
    return True


def _oxide_sum(composition: Mapping[str, float], oxides: list[str]) -> float:
    return sum(composition.get(oxide, 0.0) for oxide in oxides)


def _ceramic_match(
    ceramic_id: str,
    entry: dict[str, Any],
    entries: Mapping[str, dict[str, Any]],
) -> CeramicMatch:
    parent_id = entry.get("parent")
    return CeramicMatch(
        ceramic_id=ceramic_id,
        label=entry["label"],
        composition_kind=entry["composition"]["kind"],
        service_temp=_service_temp(entry["service_temp"]),
        liner_suitability=dict(entry["liner_suitability"]),
        parent_id=parent_id,
        match_level=str(entry.get("level") or ("parent" if parent_id is None else "subtype")),
        hierarchy=_hierarchy(ceramic_id, entry, entries),
        datasheet=dict(entry.get("datasheet") or {}),
    )


def _glass_match(
    family_id: str,
    entry: dict[str, Any],
    entries: Mapping[str, dict[str, Any]],
) -> GlassMatch:
    parent_id = entry.get("parent")
    return GlassMatch(
        family_id=family_id,
        label=entry["label"],
        parent_id=parent_id,
        match_level=str(entry.get("level") or ("parent" if parent_id is None else "subtype")),
        hierarchy=_hierarchy(family_id, entry, entries),
        composition_kind=entry["composition"]["kind"],
        use_grade=tuple(entry.get("use_grade") or ()),
        datasheet=dict(entry.get("datasheet") or {}),
    )


def _hierarchy(
    material_id: str,
    entry: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    parent_id = entry.get("parent")
    if parent_id is None:
        return (material_id,)
    if parent_id not in entries:
        raise ValueError(f"unknown parent {parent_id!r} for {material_id!r}")
    return (str(parent_id), material_id)


def _service_temp(cell: Mapping[str, Any]) -> CeramicServiceTemperature:
    kind = str(cell.get("kind", "uncharacterized"))
    value = cell.get("value_C")
    value_C = None if value is None else float(value)
    return CeramicServiceTemperature(
        value_C=value_C,
        kind=kind,
        usable_service_C=value_C if kind == "service" else None,
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
    )


def _total_fe2o3_equivalent(
    composition: Mapping[str, float], model: Mapping[str, Any]
) -> float:
    factor = float(model.get("feo_to_fe2o3_factor", 1.1113))
    return composition.get("Fe2O3", 0.0) + factor * composition.get("FeO", 0.0)


def _resolve_fe2_fraction(
    composition: Mapping[str, float],
    *,
    pO2_mbar: float | None,
    temperature_C: float | None,
    pressure_mbar: float | None,
    explicit: float | None,
) -> tuple[float | None, str]:
    if explicit is not None:
        return max(0.0, min(1.0, float(explicit))), "provided"
    feo_mol = composition.get("FeO", 0.0) / MOLAR_MASS["FeO"]
    fe2o3_mol = composition.get("Fe2O3", 0.0) / MOLAR_MASS["Fe2O3"]
    iron_atoms = feo_mol + 2.0 * fe2o3_mol
    if iron_atoms > 0.0:
        return feo_mol / iron_atoms, "ledger_speciation"
    if pO2_mbar is None or temperature_C is None or iron_atoms <= 0.0:
        return None, "unavailable"
    mol_fractions = melt_mol_fractions_for_kress91(composition)
    if not mol_fractions:
        return None, "unavailable"
    fo2_bar = max(float(pO2_mbar) / 1000.0, 1e-300)
    total_pressure_bar = floor_vacuum_pressure_bar(
        max(float(pressure_mbar or pO2_mbar), 0.0) / 1000.0
    )
    fe3_fraction = kress91_fe3_over_sigma_fe(
        fO2_log=math.log10(fo2_bar),
        mol_fractions=mol_fractions,
        T_K=float(temperature_C) + 273.15,
        pressure_bar=total_pressure_bar,
    )
    return 1.0 - float(fe3_fraction), "kress91_from_pO2"


def _band_value(value: float, bands: list[dict[str, Any]], key: str) -> str:
    for band in bands:
        if "max_exclusive" in band and value >= float(band["max_exclusive"]):
            continue
        if "max_inclusive" in band and value > float(band["max_inclusive"]):
            continue
        return str(band[key])
    raise ValueError(f"band table has no terminal {key}")


def _glass_colour(
    total_fe: float,
    fe2_fraction: float | None,
    model: Mapping[str, Any],
) -> str:
    for band in model.get("low_fe_colour_bands") or ():
        if total_fe < float(band["max_exclusive"]):
            return str(band["colour"])
    if total_fe > 3.0 and fe2_fraction is not None and fe2_fraction > 0.6:
        return "dark_brown_black"
    if fe2_fraction is None:
        return "green"
    return _band_value(fe2_fraction, model["redox_colour_bands"], "colour")


def _gated_use_grades(
    match: GlassMatch | None, clarity: str
) -> tuple[str, ...]:
    if match is None:
        return ()
    grades = list(match.use_grade)
    if clarity in {"optical_clear", "low_iron_clear"} and match.family_id != "basalt_high_fe_glass":
        if "optical" not in grades:
            grades.append("optical")
    else:
        grades = [grade for grade in grades if grade != "optical"]
    if clarity == "opaque_dark":
        grades = [grade for grade in grades if grade not in {"architectural", "container"}]
    return tuple(grades)


def _clarity_rank(value: str) -> int:
    order = (
        "optical_clear",
        "low_iron_clear",
        "standard_clear_tinted",
        "intentionally_coloured",
        "strongly_coloured",
        "opaque_dark",
    )
    return order.index(value)
