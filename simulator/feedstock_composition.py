"""Feedstock composition normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


def _representative_number(value: Any, field: str) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _valid_declared_number(float(value), field)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
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

    total = sum(kg for kg in raw_masses.values() if kg > 0.0)
    if total <= 0.0 or batch_mass_kg <= 0.0:
        return raw_masses

    scale = batch_mass_kg / total
    return {component: kg * scale for component, kg in raw_masses.items()}


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
