"""Feedstock composition normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

from simulator.scalar_boundary import is_declared_real_scalar


FEOT_FROM_FE2O3 = 2.0 * 71.844 / 159.687
OXYGEN_IN_FEO = 15.999 / 71.844
FE_REPORTING_CONVENTION_TOTAL_AS_FEO = "total Fe as FeO"
UNKNOWN_FERRIC_UPPER_BOUND_REASON = (
    "no stated maximum Fe3+/ΣFe for this body"
)


@dataclass(frozen=True)
class ResolvedFeedstockComposition:
    """Canonical oxide map and iron semantics for one feedstock entry.

    ``total_fe`` is expressed as FeO-equivalent wt% on the entry's declared
    composition basis. ``fe_metal`` is elemental Fe wt% from the separate
    elemental composition field.
    """

    canonical_wt_pct: Mapping[str, Any]
    provenance: Mapping[str, Any]
    fe_redox_split_unknown: bool
    total_fe: float
    total_fe_basis: str
    measured_feo: float | None
    measured_fe2o3: float | None
    fe_metal: float | None
    split_known: bool


@dataclass(frozen=True)
class OxygenBound:
    value_wt_pct: float | None
    refused_reason: str | None = None


@dataclass(frozen=True)
class TotalOxygenBounds:
    lower: OxygenBound
    upper: OxygenBound


def resolve_feedstock_composition(
    feedstock: Mapping[str, Any],
) -> ResolvedFeedstockComposition:
    """Validate and resolve feedstock oxide values with iron provenance."""
    composition = feedstock.get("composition_wt_pct", {}) or {}
    if not isinstance(composition, Mapping):
        raise ValueError("composition_wt_pct must be a mapping")
    basis = feedstock.get("composition_basis", {}) or {}
    if not isinstance(basis, Mapping):
        raise ValueError("composition_basis must be a mapping")

    flag = feedstock.get("fe_redox_split_unknown", False)
    if not isinstance(flag, bool):
        raise ValueError("fe_redox_split_unknown must be a boolean")

    has_feo = "FeO" in composition
    has_fe2o3 = "Fe2O3" in composition
    if "Fe" in composition or "Fe0" in composition or "Fe_metal" in composition:
        raise ValueError(
            "metallic iron must be outside composition_wt_pct"
        )
    if flag:
        if has_fe2o3:
            raise ValueError(
                "fe_redox_split_unknown cannot be combined with Fe2O3"
            )
        if not has_feo:
            raise ValueError(
                "fe_redox_split_unknown requires FeO carrying total Fe as FeO"
            )
        if basis.get("fe_reporting_convention") != (
            FE_REPORTING_CONVENTION_TOTAL_AS_FEO
        ):
            raise ValueError(
                "fe_redox_split_unknown requires composition_basis."
                "fe_reporting_convention: total Fe as FeO"
            )
    elif has_fe2o3:
        if not has_feo:
            raise ValueError("measured split requires both FeO and Fe2O3")
        for oxide in ("FeO", "Fe2O3"):
            provenance = basis.get(oxide)
            if not isinstance(provenance, Mapping) or not all(
                isinstance(provenance.get(field), str)
                and provenance[field].strip()
                for field in ("method", "source")
            ):
                raise ValueError(
                    f"measured {oxide} requires composition_basis.{oxide}."
                    "method and source"
                )

    feo = _representative_number(composition.get("FeO"), "composition_wt_pct.FeO")
    fe2o3 = _representative_number(
        composition.get("Fe2O3"), "composition_wt_pct.Fe2O3"
    )
    elemental = feedstock.get("elemental_composition_wt_pct", {}) or {}
    if not isinstance(elemental, Mapping):
        raise ValueError("elemental_composition_wt_pct must be a mapping")
    metal = _representative_number(
        elemental.get("Fe"), "elemental_composition_wt_pct.Fe"
    )
    feot = feot_equivalent_wt_pct(composition)
    if flag:
        measured_feo_value = None
        measured_fe2o3_value = None
    else:
        measured_feo_value = feo
        measured_fe2o3_value = fe2o3 if fe2o3 is not None else (0.0 if feo is not None else None)

    return ResolvedFeedstockComposition(
        canonical_wt_pct=dict(composition),
        provenance=dict(basis),
        fe_redox_split_unknown=flag,
        total_fe=feot,
        total_fe_basis="FeO-equivalent wt% on the declared composition basis",
        measured_feo=measured_feo_value,
        measured_fe2o3=measured_fe2o3_value,
        fe_metal=metal,
        split_known=not flag,
    )


def total_fe(feedstock_or_composition: Mapping[str, Any]) -> float:
    """Return total iron as FeO-equivalent wt% (the explicit basis)."""
    feo, fe2o3 = iron_oxide_values(feedstock_or_composition)
    return feo + fe2o3 * FEOT_FROM_FE2O3


def iron_oxide_values(
    feedstock_or_composition: Mapping[str, Any],
) -> tuple[float, float]:
    """Read canonical FeO and Fe2O3 values through the iron owner."""
    composition = _composition_map(feedstock_or_composition)
    feo = _representative_number(composition.get("FeO"), "composition_wt_pct.FeO")
    fe2o3 = _representative_number(
        composition.get("Fe2O3"), "composition_wt_pct.Fe2O3"
    )
    return feo or 0.0, fe2o3 or 0.0


def measured_feo(feedstock: Mapping[str, Any]) -> float | None:
    return resolve_feedstock_composition(feedstock).measured_feo


def measured_fe2o3(feedstock: Mapping[str, Any]) -> float | None:
    return resolve_feedstock_composition(feedstock).measured_fe2o3


def fe_metal(feedstock: Mapping[str, Any]) -> float | None:
    return resolve_feedstock_composition(feedstock).fe_metal


def split_known(feedstock: Mapping[str, Any]) -> bool:
    return resolve_feedstock_composition(feedstock).split_known


def total_oxygen_bounds(
    feedstock: Mapping[str, Any],
) -> TotalOxygenBounds:
    """Return iron-oxide oxygen bounds; unknown ferric maxima stay refused."""
    resolved = resolve_feedstock_composition(feedstock)
    if resolved.fe_redox_split_unknown:
        return TotalOxygenBounds(
            lower=OxygenBound(resolved.total_fe * OXYGEN_IN_FEO),
            upper=OxygenBound(None, UNKNOWN_FERRIC_UPPER_BOUND_REASON),
        )
    feo = resolved.measured_feo or 0.0
    fe2o3 = resolved.measured_fe2o3 or 0.0
    oxygen = feo * OXYGEN_IN_FEO + fe2o3 * (3.0 * 15.999 / 159.687)
    bound = OxygenBound(oxygen)
    return TotalOxygenBounds(lower=bound, upper=bound)


def feot_equivalent_wt_pct(comp_wt: Mapping[str, Any]) -> float:
    """Return FeO-equivalent wt% for separate FeO / Fe2O3 oxide values."""
    return total_fe(comp_wt)


def _composition_map(source: Mapping[str, Any]) -> Mapping[str, Any]:
    composition = source.get("composition_wt_pct")
    if isinstance(composition, Mapping):
        return composition
    return source


def _representative_number(value: Any, field: str) -> float | None:
    if is_declared_real_scalar(value) and isinstance(value, (int, float)):
        return _valid_declared_number(float(value), field)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            if not is_declared_real_scalar(
                value[0],
                allow_numeric_str=True,
            ) or not is_declared_real_scalar(
                value[1],
                allow_numeric_str=True,
            ):
                raise TypeError
            low_raw = float(value[0])
            high_raw = float(value[1])
        except (TypeError, ValueError):
            return None
        low = _valid_declared_number(low_raw, field)
        high = _valid_declared_number(high_raw, field)
        return (low + high) / 2.0
    return None


def _valid_declared_number(number: float, field: str) -> float:
    if not math.isfinite(number):
        raise ValueError(f"invalid feedstock declaration {field}: non-finite {number!r}")
    if number < 0.0:
        raise ValueError(f"invalid feedstock declaration {field}: negative {number!r}")
    return number


def normalized_feedstock_component_masses_kg(
    feedstock: Mapping[str, Any],
    mass_kg: float,
) -> dict[str, float]:
    """Return ledger-normalized raw feedstock component masses."""
    batch_mass_kg = float(mass_kg)
    raw_masses: dict[str, float] = {}
    composition = feedstock.get("composition_wt_pct", {}) or {}

    if isinstance(composition, Mapping):
        raw_masses.update(
            {
                str(component): kg
                for component, raw_value in composition.items()
                if (
                    kg := _mass_from_wt_pct(
                        raw_value,
                        batch_mass_kg,
                        field=f"composition_wt_pct.{component}",
                    )
                )
                is not None
                and kg > 0.0
            }
        )
    for section_name in ("non_oxide_components", "bulk_additions", "structural_water"):
        section = feedstock.get(section_name, {}) or {}
        if isinstance(section, Mapping):
            for raw_name, raw_value in section.items():
                name = _component_name_from_field(str(raw_name))
                kg = (
                    _mass_from_kg_per_tonne(
                        raw_value,
                        batch_mass_kg,
                        field=f"{section_name}.{raw_name}",
                    )
                    if str(raw_name).endswith("_kg_per_tonne")
                    else _mass_from_wt_pct(
                        raw_value,
                        batch_mass_kg,
                        field=f"{section_name}.{raw_name}",
                    )
                )
                if kg is not None and kg > 0.0:
                    raw_masses[name] = raw_masses.get(name, 0.0) + kg

    return normalize_component_masses_kg(raw_masses, batch_mass_kg)


def normalize_component_masses_kg(
    masses: dict[str, float],
    target_mass_kg: float,
) -> dict[str, float]:
    """Scale positive component masses to a ledger target, in place."""
    total = sum(kg for kg in masses.values() if kg > 0.0)
    if total <= 0.0 or target_mass_kg <= 0.0:
        return masses
    scale = target_mass_kg / total
    for component, kg in list(masses.items()):
        masses[component] = kg * scale
    return masses


def _mass_from_wt_pct(value: Any, mass_kg: float, *, field: str) -> float | None:
    number = _representative_number(value, field)
    if number is None:
        return None
    return mass_kg * number / 100.0


def _mass_from_kg_per_tonne(
    value: Any,
    batch_mass_kg: float,
    *,
    field: str,
) -> float | None:
    number = _representative_number(value, field)
    if number is None:
        return None
    return batch_mass_kg * number / 1000.0


def _component_name_from_field(raw_name: str) -> str:
    for suffix in ("_wt_pct", "_kg_per_tonne"):
        if raw_name.endswith(suffix):
            return raw_name[: -len(suffix)]
    return raw_name
