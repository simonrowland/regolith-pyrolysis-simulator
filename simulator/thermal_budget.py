"""Standalone thermal-budget diagnostics for bootstrap furnace sizing.

This module is intentionally golden-neutral: it has no AtomLedger, runner, or
fixture integration.  It exposes a callable decomposition that callers may use
to inspect heat-flow bookkeeping and the cold-skull active cooling floor.

Model scope:
- heat in is caller-supplied solar/electrical thermal input;
- sinks are feed sensible+fusion enthalpy, reaction/disproportionation
  enthalpy, product-vapor enthalpy, melt-surface radiation, and outer-wall
  radiation;
- cold-skull active extraction is the ideal/minimum steady-state heat-pipe or
  metal-conduction load after passive radiation to space.

Assumptions and uncertified refinements:
- process gas flow is never a cooling term; pO2 and neutral sweep remain
  chemistry controls, not furnace-wall heat sinks;
- wall conductivity, wall thickness, wall area, solidus, outer-wall
  temperature, sky temperature, and view factor are caller-supplied unless a
  caller provides source tags;
- creep, thermal shock, and mbar forced-convection coefficient are structured
  uncertified gaps and are not part of the ideal/minimum heat-balance floor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.equipment import EquipmentDesigner, STEFAN_BOLTZMANN
from simulator.furnace_materials import load_furnace_materials
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


CITED = "CITED"
ASSUMED = "ASSUMED"
UNCERTIFIED = "UNCERTIFIED"

KELVIN_OFFSET = CELSIUS_TO_KELVIN_OFFSET
MELT_EMISSIVITY = EquipmentDesigner.MELT_EMISSIVITY
THERMAL_BUDGET_VIEW_FACTOR = 1.0
THERMAL_BUDGET_WALL_CONDUCTIVITY_W_M_K = 1.5
THERMAL_BUDGET_WALL_INNER_SOLIDUS_T_C = 1050.0
UNCERTIFIED_GAP_SPECS = (
    (
        "creep",
        "Long-duration hot-wall creep life is not certified by this heat-balance floor.",
    ),
    (
        "thermal-shock",
        "Thermal-shock cycling limits are not certified by this heat-balance floor.",
    ),
    (
        "mbar-h",
        "mbar forced-convection coefficient is not certified; gas remains a chemistry trim lever, not primary cooling.",
    ),
)


def thermal_budget_decomposition(
    *,
    wall_area_m2: float,
    wall_thickness_m: float,
    wall_conductivity_W_m_K: float,
    wall_inner_solidus_T_C: float,
    wall_outer_T_C: float,
    T_sky_K: float,
    view_factor: float,
    emissivity: float = MELT_EMISSIVITY,
    heat_in_kW: float | None = None,
    feed_sensible_fusion_enthalpy_kW: float | None = None,
    reaction_disproportionation_enthalpy_kW: float | None = None,
    product_vapor_enthalpy_kW: float | None = None,
    melt_T_C: float | None = None,
    melt_surface_area_m2: float | None = None,
    source_tags: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Return tagged heat-flow terms and cold-skull active cooling floor.

    ``cold_skull_cooling_flux_kW_min`` is the ideal/minimum active extraction
    needed to hold the inner wall at the feedstock solidus:

    ``q_to_wall = k * (T_solidus - T_outer) / L``

    ``q_radiative_available = emissivity * sigma * view_factor
    * (T_outer**4 - T_sky**4)``

    ``active = max(0, q_to_wall - q_radiative_available)``

    All temperatures in the public API are Celsius except ``T_sky_K``.  All
    powers are kW; all fluxes are kW/m2.  Returned figures carry CITED,
    ASSUMED, or UNCERTIFIED status and a source note.
    """

    tags = dict(source_tags or {})

    wall_area_m2 = _positive(wall_area_m2, "wall_area_m2")
    wall_thickness_m = _positive(wall_thickness_m, "wall_thickness_m")
    wall_conductivity_W_m_K = _non_negative(
        wall_conductivity_W_m_K, "wall_conductivity_W_m_K"
    )
    view_factor = _unit_interval(view_factor, "view_factor")
    emissivity = _unit_interval(emissivity, "emissivity")

    solidus_K = _c_to_k(wall_inner_solidus_T_C, "wall_inner_solidus_T_C")
    wall_outer_K = _c_to_k(wall_outer_T_C, "wall_outer_T_C")
    T_sky_K = _kelvin(T_sky_K, "T_sky_K")

    conductive_W_per_m2 = (
        wall_conductivity_W_m_K
        * max(0.0, solidus_K - wall_outer_K)
        / wall_thickness_m
    )
    wall_heat_kW_per_m2 = conductive_W_per_m2 / 1000.0
    wall_heat_kW = wall_heat_kW_per_m2 * wall_area_m2

    outer_wall_radiative_kW_per_m2 = _radiative_flux_kW_per_m2(
        wall_outer_K,
        T_sky_K,
        emissivity=emissivity,
        view_factor=view_factor,
    )
    outer_wall_radiative_kW = outer_wall_radiative_kW_per_m2 * wall_area_m2
    cooling_flux_kW_per_m2 = max(
        0.0, wall_heat_kW_per_m2 - outer_wall_radiative_kW_per_m2
    )
    cooling_kW = cooling_flux_kW_per_m2 * wall_area_m2

    melt_surface_radiative_loss_kW = None
    if melt_T_C is not None and melt_surface_area_m2 is not None:
        melt_K = _c_to_k(melt_T_C, "melt_T_C")
        melt_surface_area_m2 = _positive(
            melt_surface_area_m2, "melt_surface_area_m2"
        )
        melt_surface_radiative_loss_kW = (
            _radiative_flux_kW_per_m2(
                melt_K,
                T_sky_K,
                emissivity=emissivity,
                view_factor=view_factor,
            )
            * melt_surface_area_m2
        )

    heat_flows_kW = {
        "heat_in": _optional_non_negative(heat_in_kW, "heat_in_kW"),
        "feed_sensible_fusion_enthalpy_sink": _optional_non_negative(
            feed_sensible_fusion_enthalpy_kW,
            "feed_sensible_fusion_enthalpy_kW",
        ),
        "reaction_disproportionation_enthalpy_sink": _optional_non_negative(
            reaction_disproportionation_enthalpy_kW,
            "reaction_disproportionation_enthalpy_kW",
        ),
        "product_vapor_enthalpy_sink": _optional_non_negative(
            product_vapor_enthalpy_kW,
            "product_vapor_enthalpy_kW",
        ),
        "melt_surface_radiative_loss": melt_surface_radiative_loss_kW,
        "outer_wall_radiative_loss": outer_wall_radiative_kW,
    }
    heat_flows_kW["net_unallocated"] = _net_unallocated(heat_flows_kW)

    figures = {
        "stefan_boltzmann_W_m2_K4": _figure(
            STEFAN_BOLTZMANN,
            "W/(m2 K4)",
            _tag(
                tags,
                "stefan_boltzmann_W_m2_K4",
                CITED,
                "simulator.equipment.STEFAN_BOLTZMANN",
            ),
        ),
        "emissivity": _figure(
            emissivity,
            "dimensionless",
            _cited_default_or_caller_supplied_tag(
                emissivity,
                MELT_EMISSIVITY,
                "simulator.equipment.EquipmentDesigner.MELT_EMISSIVITY",
            ),
        ),
        "view_factor": _figure(
            view_factor,
            "dimensionless",
            _cited_default_or_caller_supplied_tag(
                view_factor,
                THERMAL_BUDGET_VIEW_FACTOR,
                "simulator.accounting.queries._wall_geometry_conductance_weight view_factor_from_melt default",
            ),
        ),
        "wall_area_m2": _figure(
            wall_area_m2,
            "m2",
            _tag(tags, "wall_area_m2", ASSUMED, "caller supplied"),
        ),
        "wall_thickness_m": _figure(
            wall_thickness_m,
            "m",
            _tag(tags, "wall_thickness_m", ASSUMED, "caller supplied"),
        ),
        "wall_conductivity_W_m_K": _figure(
            wall_conductivity_W_m_K,
            "W/(m K)",
            _cited_default_or_caller_supplied_tag(
                wall_conductivity_W_m_K,
                THERMAL_BUDGET_WALL_CONDUCTIVITY_W_M_K,
                "bootstrap heat-balance reference: wall conductivity 1.5 W/(m K)",
            ),
        ),
        "wall_inner_solidus_T_C": _figure(
            wall_inner_solidus_T_C,
            "degC",
            _cited_default_or_caller_supplied_tag(
                wall_inner_solidus_T_C,
                THERMAL_BUDGET_WALL_INNER_SOLIDUS_T_C,
                "bootstrap heat-balance reference: solidus about 1050 C",
            ),
        ),
        "wall_outer_T_C": _figure(
            wall_outer_T_C,
            "degC",
            _tag(tags, "wall_outer_T_C", ASSUMED, "caller supplied"),
        ),
        "T_sky_K": _figure(
            T_sky_K,
            "K",
            _tag(tags, "T_sky_K", ASSUMED, "caller supplied"),
        ),
    }

    return {
        "schema": "thermal_budget_decomposition.v0",
        "status": "diagnostic_golden_neutral",
        "figures": figures,
        "heat_flows_kW": {
            key: _tagged_heat_term(key, value, tags)
            for key, value in heat_flows_kW.items()
        },
        "cold_skull": {
            "q_to_wall_kW": wall_heat_kW,
            "q_to_wall_kW_per_m2": wall_heat_kW_per_m2,
            "outer_wall_radiative_capacity_kW": outer_wall_radiative_kW,
            "outer_wall_radiative_capacity_kW_per_m2": (
                outer_wall_radiative_kW_per_m2
            ),
            "cold_skull_cooling_flux_kW_min": cooling_kW,
            "cold_skull_cooling_flux_kW_per_m2": cooling_flux_kW_per_m2,
            "status": "NOTICE",
            "basis": (
                "ideal/minimum active metal-conduction or heat-pipe extraction; "
                "passive radiation to space credited; process gas excluded"
            ),
        },
        "uncertified_gaps": _uncertified_gaps(),
        "notices": [
            (
                "Process gas, neutral sweep, and pO2 are excluded from cooling; "
                "mbar gas flow is a chemistry lever here, not a heat sink."
            ),
            (
                "Cooling flux is a thermodynamic floor only; creep, thermal "
                "shock, and mbar forced-convection coefficient remain uncertified."
            ),
        ],
    }


def furnace_material_context(
    material_id: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return material max-service context and conductivity certification state."""

    raw_catalog = catalog or load_furnace_materials()
    items = raw_catalog.get("furnace_materials", raw_catalog)
    material = items.get(material_id)
    if not isinstance(material, Mapping):
        raise ValueError(f"unknown furnace material: {material_id}")

    conductivity = material.get("conductivity_W_m_K")
    conductivity_status = CITED if conductivity is not None else UNCERTIFIED
    conductivity_source = (
        f"data/furnace_materials.yaml:{material_id}.conductivity_W_m_K"
        if conductivity is not None
        else "data/furnace_materials.yaml has no conductivity_W_m_K field"
    )
    return {
        "material_id": material_id,
        "display_name": material.get("display_name"),
        "max_service_T_C": _figure(
            material.get("max_service_T_C"),
            "degC",
            {
                "status": CITED if material.get("max_service_T_C") is not None else UNCERTIFIED,
                "source": f"data/furnace_materials.yaml:{material_id}.max_service_T_C",
            },
        ),
        "conductivity_W_m_K": _figure(
            conductivity,
            "W/(m K)",
            {"status": conductivity_status, "source": conductivity_source},
        ),
        "source_note": material.get("source_note"),
    }


def _radiative_flux_kW_per_m2(
    hot_K: float,
    cold_K: float,
    *,
    emissivity: float,
    view_factor: float,
) -> float:
    return max(
        0.0,
        emissivity
        * STEFAN_BOLTZMANN
        * view_factor
        * (hot_K**4 - cold_K**4)
        / 1000.0,
    )


def _net_unallocated(heat_flows_kW: Mapping[str, float | None]) -> float | None:
    heat_in = heat_flows_kW["heat_in"]
    sinks = [
        value
        for key, value in heat_flows_kW.items()
        if key != "heat_in" and key != "net_unallocated"
    ]
    if heat_in is None or any(value is None for value in sinks):
        return None
    return heat_in - sum(value for value in sinks if value is not None)


def _tagged_heat_term(
    key: str,
    value: float | None,
    tags: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    status = UNCERTIFIED if value is None else ASSUMED
    source = "not supplied" if value is None else "caller supplied"
    return _figure(value, "kW", _tag(tags, key, status, source))


def _tag(
    tags: Mapping[str, Mapping[str, str]],
    key: str,
    default_status: str,
    default_source: str,
) -> dict[str, str]:
    tag = tags.get(key, {})
    return {
        "status": str(tag.get("status", default_status)),
        "source": str(tag.get("source", default_source)),
    }


def _cited_default_or_caller_supplied_tag(
    value: float,
    cited_value: float,
    cited_source: str,
) -> dict[str, str]:
    if math.isclose(value, cited_value, rel_tol=1e-9):
        return {"status": CITED, "source": cited_source}

    return {"status": ASSUMED, "source": "caller supplied"}


def _uncertified_gaps() -> list[dict[str, str]]:
    return [
        {"name": name, "status": UNCERTIFIED, "reason": reason}
        for name, reason in UNCERTIFIED_GAP_SPECS
    ]


def _figure(value: Any, unit: str, tag: Mapping[str, str]) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "status": tag["status"],
        "source": tag["source"],
    }


def _positive(value: float, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be > 0")
    return number


def _non_negative(value: float, name: str) -> float:
    number = _finite(value, name)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0")
    return number


def _optional_non_negative(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    return _non_negative(value, name)


def _unit_interval(value: float, name: str) -> float:
    number = _finite(value, name)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return number


def _c_to_k(value_C: float, name: str) -> float:
    return _kelvin(_finite(value_C, name) + KELVIN_OFFSET, name)


def _kelvin(value_K: float, name: str) -> float:
    number = _finite(value_K, name)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0 K")
    return number


def _finite(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number
