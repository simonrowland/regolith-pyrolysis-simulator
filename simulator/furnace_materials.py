"""Furnace material catalog and max-temperature resolver."""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_FURNACE_MATERIALS_PATH = DATA_DIR / "furnace_materials.yaml"
FURNACE_MAX_T_BOUNDS_C = (1200.0, 2000.0)


@lru_cache(maxsize=4)
def load_furnace_materials(
    path: Path | str = DEFAULT_FURNACE_MATERIALS_PATH,
) -> dict[str, Any]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"required furnace material catalog missing: {source_path}")
    with source_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    catalog = data.get("furnace_materials")
    if not isinstance(catalog, dict):
        raise ValueError(f"furnace material catalog is malformed: {source_path}")
    return catalog


def resolve_furnace_max_T_C(
    material_id: str,
    requested_cap: float | int | None = None,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> float:
    return resolve_furnace_temperature_caps(
        material_id,
        requested_cap,
        catalog=catalog,
    )["effective_applied_ceiling_T_C"]


def resolve_furnace_temperature_caps(
    material_id: str,
    requested_cap: float | int | None = None,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    materials = _catalog_items(catalog or load_furnace_materials())
    material = materials.get(str(material_id))
    if not isinstance(material, Mapping):
        raise ValueError(f"unknown furnace material: {material_id}")
    if material.get("enabled") is not True:
        reason = str(material.get("not_selectable_reason") or "disabled")
        raise ValueError(f"{material_id} not selectable yet: {reason}")

    material_max = _finite_float(
        material.get("max_service_T_C"),
        f"{material_id}.max_service_T_C",
    )
    requested_ceiling = material_max
    if requested_cap is None:
        effective_ceiling = material_max
    else:
        requested_ceiling = _finite_float(requested_cap, "requested_cap")
        effective_ceiling = min(requested_ceiling, material_max)
    # BUG-076: the resolver must never emit an effective ceiling the shared runtime
    # envelope FURNACE_MAX_T_BOUNDS_C rejects -- otherwise a resolver call site and the
    # CampaignManager envelope guard (simulator/campaigns.py:135-145) disagree on
    # admissibility, which is the cross-layer trap this bug is about. The two bounds are
    # handled ASYMMETRICALLY, on purpose:
    #   * Ceiling: a material rated above the envelope (e.g. zirconia_ysz at 2200 C)
    #     keeps its raw service_rating_T_C, but the applied ceiling is CLAMPED DOWN to
    #     the envelope max. Nobody requested the over-max temperature -- it is the
    #     material's rating, not operator intent -- so clamping down to the highest
    #     modelable temperature loses no intent and the derating stays visible
    #     (service_rating_T_C 2200 vs effective_applied_ceiling_T_C 2000).
    #   * Floor: a resolved ceiling below the envelope floor (a sub-floor *requested*
    #     cap, or a mis-catalogued enabled material) FAILS LOUD, never clamps up.
    #     Silently raising a sub-floor request would run the furnace HOTTER than the
    #     operator asked -- a silent rewrite of intent the mandate forbids.
    effective_ceiling = min(effective_ceiling, FURNACE_MAX_T_BOUNDS_C[1])
    if effective_ceiling < FURNACE_MAX_T_BOUNDS_C[0]:
        raise ValueError(
            f"{material_id}: resolved applied ceiling {effective_ceiling:.0f} C is below "
            f"the runtime envelope floor {FURNACE_MAX_T_BOUNDS_C[0]:.0f} C "
            f"(requested_cap={requested_cap}); not runtime-admissible"
        )
    return {
        "service_rating_T_C": material_max,
        "requested_ceiling_T_C": requested_ceiling,
        "effective_applied_ceiling_T_C": effective_ceiling,
    }


def _catalog_items(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = catalog.get("furnace_materials")
    if isinstance(nested, Mapping):
        return nested
    return catalog


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result
