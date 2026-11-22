"""Layered, source-aware collector and duct wall.

The owner design (2026-11-21) is a hot-face liner, a ceramic-felt middle,
and a CaO-based structural shell. Stage 3 is not that stack: its capture
surface is the fused-silica product, so its bulk is priced from the
existing stage thickness and density slots as in-situ silica.

Missing catalog numbers stay unavailable. This module does not invent a
conductivity, a density, a thickness, or an in-situ USD/kg. Liner material
is an operator setting (t-472), read from the cost block, and is not an
optimizer knob.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.condensation_routing import (
    COLLECTOR_STAGE_KEY_BY_NUMBER,
    K_TRAP_STAGE_KEY,
    STAGE_KEY_BY_NUMBER,
)
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
    PA_PER_MBAR,
    STEFAN_BOLTZMANN,
)

# Cost-area key, public stage number, collector train key. The train keys
# come from the routing owner. The area keys are the cost-parameter slots;
# a test zips them to the condensation area registries. Stage 4b has no
# integer in STAGE_KEY_BY_NUMBER because stage 4 already owns that map.
# This join is which area sees which band. It is not a second area law.
COLLECTOR_STAGE_JOIN: tuple[tuple[str, int | None, str], ...] = (
    ("fe_stage1", 1, STAGE_KEY_BY_NUMBER[1]),
    ("cr_stage2", 2, STAGE_KEY_BY_NUMBER[2]),
    ("sio_stage3", 3, STAGE_KEY_BY_NUMBER[3]),
    ("alkali_stage4", 4, STAGE_KEY_BY_NUMBER[4]),
    ("k_stage4b", None, K_TRAP_STAGE_KEY),
)
HOT_DUCT_TRAIN_KEY = COLLECTOR_STAGE_KEY_BY_NUMBER[0]
_AREA_KEYS = tuple(row[0] for row in COLLECTOR_STAGE_JOIN)
_STAGE3_AREA_KEY = "sio_stage3"

# YAML mapping key -> role emitted on the layer. Felt is the insulation role.
_LAYER_KEYS = (
    ("liner", "liner"),
    ("felt", "insulation"),
    ("structure", "structure"),
)
_ROLE_BY_KEY = dict(_LAYER_KEYS)
_KEY_BY_ROLE = {role: key for key, role in _LAYER_KEYS}
_SOURCES = frozenset({"imported", "in_situ"})
_PROPERTY_STATUSES = frozenset({"assumed", "cited"})
_CAO_BEARING_MATERIAL_IDS = frozenset({"doloma"})
_LAYER_FIELDS = frozenset({
    "role",
    "key",
    "material_id",
    "source",
    "thickness_m",
    "intended_material",
    "catalog_status",
    "density_kg_m3",
    "density_status",
    "density_citation",
    "conductivity_W_m_K",
    "conductivity_status",
    "conductivity_citation",
    "thermal_conductivity",
})
_WALL_FIELDS = frozenset({
    "source_tag",
    "outer_temperature_C",
    "outer_temperature_status",
    "layers",
    "stage_bulk",
})
_STAGE_BULK_FIELDS = frozenset({"kind", "material_id", "source"})
_CURVE_FIELDS = frozenset({"temperature_K", "value_W_m_K", "citation"})
_N2_TABLE_ID = "N-023"


def normalize_layered_wall(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return the operator wall with nulls filled and roles ordered.

    Shape errors raise. A missing thickness, density, conductivity, felt
    source, or outer temperature stays null so the pricer can refuse it.
    """

    if not isinstance(raw, Mapping):
        raise TypeError("layered_wall must be a mapping")
    extra = set(raw).difference(_WALL_FIELDS)
    if extra:
        raise ValueError(f"layered_wall has unsupported keys: {sorted(extra)}")
    source_tag = raw.get("source_tag")
    if not isinstance(source_tag, str) or not source_tag.strip():
        raise ValueError("layered_wall source_tag must be a non-empty string")
    outer = raw.get("outer_temperature_C")
    if outer is not None:
        outer = _finite_number(outer, "layered_wall.outer_temperature_C")
    outer_status = raw.get("outer_temperature_status")
    if outer_status is not None and (
        not isinstance(outer_status, str) or not outer_status.strip()
    ):
        raise ValueError("layered_wall outer_temperature_status must be a string")
    return {
        "source_tag": source_tag,
        "outer_temperature_C": outer,
        "outer_temperature_status": outer_status,
        "layers": _normalize_layers(raw.get("layers")),
        "stage_bulk": _normalize_stage_bulk(raw.get("stage_bulk", {})),
    }


def price_collector_stages(
    stage_areas_m2: Mapping[str, Any],
    *,
    layered_wall: Mapping[str, Any],
    import_delivery_cost_per_kg: float,
    in_situ_bootstrap_cost_per_kg: float | None,
    stage_wall_thickness_m: float,
    stage_bulk_density_kg_m3: Mapping[str, float],
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Price each collector by source. Stage 3 is in-situ product bulk."""

    wall = normalize_layered_wall(layered_wall)
    catalog = _catalog(materials)
    areas = _validated_areas(stage_areas_m2)
    if areas is None:
        reason = _area_problem(stage_areas_m2)
        return _closed_summary(reason)
    rows: dict[str, Any] = {}
    reasons: list[str] = []
    for area_key, _number, _train_key in COLLECTOR_STAGE_JOIN:
        if area_key == _STAGE3_AREA_KEY and _STAGE3_AREA_KEY in wall["stage_bulk"]:
            row = _price_product_bulk(
                area_key,
                areas[area_key],
                wall=wall,
                thickness_m=stage_wall_thickness_m,
                density_kg_m3=stage_bulk_density_kg_m3.get(area_key),
                import_delivery_cost_per_kg=import_delivery_cost_per_kg,
                in_situ_bootstrap_cost_per_kg=in_situ_bootstrap_cost_per_kg,
                materials=catalog,
            )
        else:
            row = _price_layered_sheet(
                area_key,
                areas[area_key],
                wall=wall,
                import_delivery_cost_per_kg=import_delivery_cost_per_kg,
                in_situ_bootstrap_cost_per_kg=in_situ_bootstrap_cost_per_kg,
                materials=catalog,
            )
        rows[area_key] = row
        if _is_unavailable(row["cost_usd"]):
            reasons.append(_reason_of(row["cost_usd"]))
    total = _sum_money(rows, reasons)
    return {
        "status": "ok" if not _is_unavailable(total) else "unavailable",
        "collector_stage_costs": rows,
        "collector_stage_cost_usd": total,
        "hardware_capital_cost_usd": total,
        "imported_kg": _sum_field(rows, "imported_kg"),
        "in_situ_kg": _sum_field(rows, "in_situ_kg"),
        "imported_usd": _sum_field(rows, "imported_usd"),
        "in_situ_usd": _sum_field(rows, "in_situ_usd"),
        "collector_stage_cost_source_status": (
            "t-1911 layered wall; imported mass at the ratified delivery "
            "price; in-situ mass at the owner-set bootstrap price"
        ),
        "reason": _join(reasons) if reasons else None,
    }


def price_duct_wall(
    *,
    diameter_m: float,
    layered_wall: Mapping[str, Any],
    import_delivery_cost_per_kg: float,
    in_situ_bootstrap_cost_per_kg: float | None,
    length_m: float | None = None,
    length_basis: str = "configured",
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Price the hot duct as concentric cylindrical shells.

    ``length_basis="per_metre"`` reports kilograms and dollars per metre.
    The 1 m factor is that unit, not a certified duct length.
    """

    wall = normalize_layered_wall(layered_wall)
    if length_basis == "per_metre":
        if length_m not in (None, 1.0):
            raise ValueError("per-metre duct price uses unit length")
        used_length_m = 1.0
        certified_length = False
        mass_units = "kg/m"
    elif length_basis == "configured":
        used_length_m = _finite_number(length_m, "hot_duct_length_m")
        if used_length_m <= 0.0:
            return _duct_closed("invalid_hardware_sizing_inputs")
        certified_length = True
        mass_units = "kg"
    else:
        raise ValueError("length_basis must be configured or per_metre")
    radius_m = _finite_number(diameter_m, "pipe_diameter_m") / 2.0
    if radius_m <= 0.0:
        return _duct_closed("gas_path_radius_invalid")
    catalog = _catalog(materials)
    layers = [
        resolve_layer_properties(layer, materials=catalog)
        for layer in wall["layers"]
    ]
    priced, radius_out = _cylindrical_masses(layers, radius_m, used_length_m)
    split = _source_split(
        priced,
        import_delivery_cost_per_kg=import_delivery_cost_per_kg,
        in_situ_bootstrap_cost_per_kg=in_situ_bootstrap_cost_per_kg,
    )
    flags = stack_failure_flags(layers, materials=catalog)
    return {
        "status": "ok" if not _is_unavailable(split["cost_usd"]) else "unavailable",
        "geometry": "cylindrical_shell",
        "length_basis": length_basis,
        "length_m": used_length_m,
        "length_certified": certified_length,
        "gas_path_radius_m": radius_m,
        "gas_path_radius_basis": (
            "configured pipe diameter / 2, used as the gas-path radius"
        ),
        "outer_radius_m": radius_out,
        "mass_units": mass_units,
        "layers": priced,
        "duct_price_basis": "layered_cylindrical_mass",
        "hot_duct_area_price_applied": False,
        "failure_flags": flags,
        **split,
    }


def flat_wall_conduction(
    layers: Sequence[Mapping[str, Any]],
    t_in_C: float,
    t_out_C: float,
) -> dict[str, Any]:
    """Steady conduction through a flat layered wall.

    Premise: steady state, no heat generation, one heat flux through every
    layer, and one conductivity per layer. The caller resolves k(T) before
    this function. A missing conductivity is refused here and is not
    treated as zero.

    Algebra: layer resistance R_i = t_i / k_i. The layers are in series,
    so R = sum(R_i) and q = (T_in - T_out) / R. The temperature walk is
    T_{i+1} = T_i - q R_i.

    Units: thickness in m, conductivity in W/(m·K), resistance in m²·K/W,
    heat flux in W/m². A temperature difference in °C equals the same
    difference in K.

    Sanity limit: one layer is Fourier's law, q = k (T_in - T_out) / t.
    A zero temperature difference gives q = 0. Conductivity of zero is
    refused, because it would report an infinite resistance and a heat
    flux of zero, which is an adiabatic wall the inputs did not state.
    """

    ready, reasons = _ready_conduction_layers(layers)
    if reasons:
        return _conduction_unavailable(reasons)
    t_in = _finite_number(t_in_C, "t_in_C")
    t_out = _finite_number(t_out_C, "t_out_C")
    resistances = [
        layer["thickness_m"] / layer["conductivity_W_m_K"] for layer in ready
    ]
    resistance = sum(resistances)
    # q [W/m²] = ΔT [K] / R [m²·K/W].
    heat_flux = (t_in - t_out) / resistance
    interfaces = _temperature_walk(ready, resistances, t_in, heat_flux, t_out)
    return {
        "status": "ok",
        "geometry": "flat",
        "heat_flux_W_m2": heat_flux,
        "resistance_m2_K_W": resistance,
        "layer_resistance_m2_K_W": resistances,
        "interface_temperatures_C": interfaces,
    }


def cylindrical_wall_conduction(
    layers: Sequence[Mapping[str, Any]],
    r_in_m: float,
    t_in_C: float,
    t_out_C: float,
) -> dict[str, Any]:
    """Steady radial conduction per metre of a cylindrical layered wall.

    Premise: steady state, no generation, axisymmetric flow, one
    conductivity per layer, and the same heat flow per unit length through
    every layer. The inner radius is the gas-path radius.

    Algebra: a layer from r_in to r_out = r_in + t has
    R' = ln(r_out / r_in) / (2 π k). Then Q' = (T_in - T_out) / sum(R').
    The walk is T_{i+1} = T_i - Q' R'_i.

    Units: radius and thickness in m, conductivity in W/(m·K), resistance
    in K·m/W, heat flow in W/m.

    Sanity limit: a thin layer has ln(1 + t/r) ≈ t/r, so
    Q' ≈ 2 π r k (T_in - T_out) / t, the flat-wall flux times the inner
    perimeter. The relative gap of that approximation is about t / (2 r).
    Conductivity of zero is refused for the same reason as the flat wall.
    """

    radius = _finite_number(r_in_m, "gas_path_radius_m")
    if radius <= 0.0:
        return _conduction_unavailable(["gas_path_radius_invalid"])
    ready, reasons = _ready_conduction_layers(layers)
    if reasons:
        return _conduction_unavailable(reasons)
    t_in = _finite_number(t_in_C, "t_in_C")
    t_out = _finite_number(t_out_C, "t_out_C")
    resistances: list[float] = []
    cursor = radius
    for layer in ready:
        outer = cursor + layer["thickness_m"]
        # R' [K·m/W] = ln(r_out / r_in) / (2 π k [W/(m·K)]).
        resistances.append(
            math.log(outer / cursor) / (2.0 * math.pi * layer["conductivity_W_m_K"])
        )
        cursor = outer
    resistance = sum(resistances)
    heat_flow = (t_in - t_out) / resistance
    interfaces = _temperature_walk(ready, resistances, t_in, heat_flow, t_out)
    return {
        "status": "ok",
        "geometry": "cylindrical",
        "heat_flow_W_per_m": heat_flow,
        "resistance_K_m_W": resistance,
        "layer_resistance_K_m_W": resistances,
        "interface_temperatures_C": interfaces,
        "outer_radius_m": cursor,
    }


def resolve_layer_properties(
    layer: Mapping[str, Any],
    *,
    materials: Mapping[str, Any] | None = None,
    temperature_K: float | None = None,
) -> dict[str, Any]:
    """Resolve one layer. Gaps are reasons. Conflicts raise."""

    catalog = _catalog(materials)
    material_id = layer.get("material_id")
    entry = None
    unknown = None
    if material_id is None:
        unknown = f"catalog_missing:{layer['role']}"
    else:
        entry = catalog.get(str(material_id))
        if not isinstance(entry, Mapping):
            unknown = f"unknown_material:{material_id}"
            entry = None
    density, density_reason, density_flag = _resolve_density(layer, entry)
    conductivity, conductivity_reason, conductivity_flag = _resolve_conductivity(
        layer,
        entry,
        temperature_K=temperature_K,
    )
    service = _service_record(entry)
    gas_tight = _gas_tight_record(entry)
    return {
        "role": layer["role"],
        "key": layer.get("key", _KEY_BY_ROLE[layer["role"]]),
        "material_id": material_id,
        "source": layer.get("source"),
        "thickness_m": layer.get("thickness_m"),
        "intended_material": layer.get("intended_material"),
        "catalog_status": layer.get("catalog_status"),
        "unknown_reason": unknown,
        "density_kg_m3": density,
        "density_reason": density_reason,
        "density_flag": density_flag,
        "conductivity_W_m_K": conductivity,
        "conductivity_reason": conductivity_reason,
        "conductivity_flag": conductivity_flag,
        "service": service,
        "gas_tight": gas_tight,
        "chemical_attack_sio": _sio_attack(entry),
    }


def stack_failure_flags(
    layers: Sequence[Mapping[str, Any]],
    *,
    materials: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Liner tightness, in-felt condensation, CaO attack, and recession."""

    del materials
    flags: list[dict[str, Any]] = []
    liner = next((layer for layer in layers if layer["role"] == "liner"), None)
    structure = next(
        (layer for layer in layers if layer["role"] == "structure"), None
    )
    gas_facing = liner or (layers[0] if layers else None)
    flags.extend(_gas_tight_flags(gas_facing, product_surface=False))
    if any(layer["role"] == "insulation" for layer in layers):
        flags.append({
            "code": "vapour_condensation_inside_insulation_unchecked",
            "status": "flagged",
            "detail": (
                "The stack has a porous middle and no certified dew-point "
                "barrier. Vapour that crosses the liner would condense inside "
                "the felt where the wall drops through the dew point. No "
                "condensation location is computed."
            ),
            "citations": [],
        })
    structure_is_cao = _is_cao_bearing(structure)
    if structure_is_cao:
        flags.append(_cao_attack_flag(structure))
        if liner is None or liner.get("thickness_m") is None:
            flags.append({
                "code": "liner_thickness_unset",
                "status": "flagged",
                "detail": "The hot-face liner thickness is unset.",
                "citations": [],
            })
            flags.append({
                "code": "cao_shell_not_behind_intact_liner",
                "status": "flagged",
                "detail": (
                    "Liner thickness is unset, so the CaO shell cannot be "
                    "claimed to sit behind an intact liner."
                ),
                "citations": [],
            })
    flags.extend(_recession_flags(gas_facing, structure_is_cao=structure_is_cao))
    if _is_cao_bearing(gas_facing):
        flags.append({
            "code": "cao_on_gas_path",
            "status": "flagged",
            "detail": (
                "The gas-facing layer is CaO-bearing doloma. Keep CaO behind "
                "the liner and out of the gas path."
            ),
            "citations": ["t-476"],
        })
    return flags


def product_surface_flags(
    material_id: str | None,
    *,
    materials: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Stage-3 baffle flags. The priced wall is the product, not the stack."""

    catalog = _catalog(materials)
    entry = catalog.get(str(material_id)) if material_id is not None else None
    if not isinstance(entry, Mapping):
        entry = None
    facing = {
        "role": "product_surface",
        "material_id": material_id,
        "gas_tight": _gas_tight_record(entry if isinstance(entry, Mapping) else None),
        "thickness_m": None,
    }
    flags = _gas_tight_flags(facing, product_surface=True)
    flags.append({
        "code": "vapour_condensation_inside_insulation_unchecked",
        "status": "not_applicable_no_insulation_layer",
        "detail": (
            "The priced stage-3 wall is the product baffle only. "
            "It has no insulation layer in which vapour could condense."
        ),
        "citations": [],
    })
    # Same recession rule as the stack: it follows the gas-facing material.
    flags.extend(_recession_flags(facing, structure_is_cao=False))
    if _is_cao_bearing(facing):
        flags.append({
            "code": "cao_on_gas_path",
            "status": "flagged",
            "detail": (
                "The stage-3 gas-facing surface is CaO-bearing. Keep CaO "
                "out of the gas path. The t-476 screen applies; no rate "
                "is computed."
            ),
            "citations": ["t-476"],
        })
    return flags


def flue_gas_energy_balance(
    *,
    gas_temperature_C: float,
    pressure_mbar_low: float,
    pressure_mbar_high: float,
    pipe_diameter_m: float,
    carrier_gas: str,
    layered_wall: Mapping[str, Any],
    campaign: str,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare duct loss with what the 5–15 mbar flue can be shown to carry.

    Conductive loss uses the layered wall. A missing conductivity refuses
    the loss. The gas side uses the ideal-gas density and the two printed
    JANAF Cp rows that bracket the gas temperature. It does not interpolate.
    Radiation delivered to the liner is unavailable; σT^4 is only an upper
    bound. Stream power is unavailable while the mass flow is unset, so the
    hot-face hold and the trace-heating power stay undetermined.
    """

    wall = normalize_layered_wall(layered_wall)
    temperature_C = _finite_number(gas_temperature_C, "gas_temperature_C")
    temperature_K = temperature_C + CELSIUS_TO_KELVIN_OFFSET
    low_mbar = _finite_number(pressure_mbar_low, "pressure_mbar_low")
    high_mbar = _finite_number(pressure_mbar_high, "pressure_mbar_high")
    diameter_m = _finite_number(pipe_diameter_m, "pipe_diameter_m")
    if low_mbar <= 0.0 or high_mbar <= 0.0 or diameter_m <= 0.0:
        raise ValueError("flue pressure and diameter must be positive")
    if temperature_K <= 0.0:
        raise ValueError("flue gas temperature must be above absolute zero")
    radius_m = diameter_m / 2.0
    catalog = _catalog(materials)
    resolved = [
        resolve_layer_properties(
            layer,
            materials=catalog,
            temperature_K=temperature_K,
        )
        for layer in wall["layers"]
    ]
    _ready, layer_reasons = _ready_conduction_layers(resolved)
    conduction_reasons = list(layer_reasons)
    if wall["outer_temperature_C"] is None:
        conduction_reasons.append("outer_temperature_unset")
    if conduction_reasons:
        conduction = _conduction_unavailable(conduction_reasons)
    else:
        conduction = cylindrical_wall_conduction(
            resolved,
            radius_m,
            temperature_C,
            wall["outer_temperature_C"],
        )
    # The campaign carrier token has to be N2 before the N2 table applies.
    # "pN2" is not that token.
    if carrier_gas != "N2":
        cp_bounds = _unavailable(
            f"carrier_cp_table_refused:{carrier_gas}",
            "J/(mol·K)",
        )
        densities = _unavailable(
            f"carrier_cp_table_refused:{carrier_gas}",
            "kg/m3",
        )
    else:
        cp_bounds = _n2_cp_bounds(temperature_K)
        densities = {
            "low_mbar": _ideal_gas_density_kg_m3(
                low_mbar * PA_PER_MBAR, temperature_K
            ),
            "high_mbar": _ideal_gas_density_kg_m3(
                high_mbar * PA_PER_MBAR, temperature_K
            ),
        }
    volumetric = _volumetric_heat(densities, cp_bounds)
    # Black-body emission of a surface at the gas temperature.
    # Premise: a black surface emits σ T^4. Emissivity 1 and view factor 1
    # are an upper bound, not the furnace's view of the liner.
    # Algebra: q = σ T^4.
    # Units: σ in W/(m²·K⁴), T in K, q in W/m².
    # Sanity limit: the flux is zero at 0 K and scales as the fourth power.
    black_body = STEFAN_BOLTZMANN * temperature_K ** 4
    if conduction["status"] != "ok":
        trace_reason = "conductive_loss_unavailable"
    else:
        trace_reason = "mass_flow_unset"
    return {
        "campaign": campaign,
        "carrier_gas": carrier_gas,
        "gas_temperature_C": temperature_C,
        "gas_temperature_K": temperature_K,
        "pressure_mbar": [low_mbar, high_mbar],
        "pipe_diameter_m": diameter_m,
        "length_basis": "per_metre_of_duct",
        "pipe_length_status": "unset_in_setpoints",
        "ideal_gas_density_kg_m3": densities,
        "n2_cp_bounds": cp_bounds,
        "volumetric_heat_J_per_m3_K": volumetric,
        "inventory_heat_J_per_m_K": _inventory_heat(volumetric, radius_m),
        "stream_power_W": _unavailable("mass_flow_unset", "W"),
        "conductive_loss": conduction,
        "radiation_delivered_W_m2": _unavailable("view_factor_unset", "W/m2"),
        "black_body_flux_W_m2": {
            "value": black_body,
            "status": "assumed_upper_bound",
            "emissivity": 1.0,
            "view_factor": 1.0,
            "detail": (
                "σT^4 at the gas temperature with emissivity 1 and view "
                "factor 1. Not a furnace-to-liner coupling."
            ),
        },
        "verdict": "hot_face_hold_undetermined",
        "trace_heating_power_W_per_m": _unavailable(trace_reason, "W/m"),
        "trace_heating_verdict": "trace_heating_power_unavailable",
    }


def shipped_design_report(
    cost_parameters: Any | None = None,
    *,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Design-point split, interface table, and flue-gas hold check."""

    parameters = _cost_parameters(cost_parameters)
    setpoints = _setpoints()
    catalog = _catalog(materials)
    areas = _setpoint_areas(setpoints)
    bands = _collector_bands(setpoints)
    priced = price_collector_stages(
        areas,
        layered_wall=parameters.layered_wall,
        import_delivery_cost_per_kg=parameters.import_delivery_cost_per_kg,
        in_situ_bootstrap_cost_per_kg=parameters.in_situ_bootstrap_cost_per_kg,
        stage_wall_thickness_m=parameters.condenser_stage_wall_thickness_m,
        stage_bulk_density_kg_m3=parameters.condenser_stage_bulk_density_kg_m3,
        materials=catalog,
    )
    duct = price_duct_wall(
        diameter_m=_sweep_diameter(setpoints),
        layered_wall=parameters.layered_wall,
        import_delivery_cost_per_kg=parameters.import_delivery_cost_per_kg,
        in_situ_bootstrap_cost_per_kg=parameters.in_situ_bootstrap_cost_per_kg,
        length_basis="per_metre",
        materials=catalog,
    )
    return {
        "collector_stages": priced,
        "hot_duct_per_metre": duct,
        "interface_temperatures": interface_temperature_table(
            parameters.layered_wall,
            bands,
            materials=catalog,
        ),
        "flue_gas_energy_balance": [
            flue_gas_energy_balance(
                gas_temperature_C=row["gas_temperature_C"],
                pressure_mbar_low=row["pressure_mbar"][0],
                pressure_mbar_high=row["pressure_mbar"][1],
                pipe_diameter_m=row["pipe_diameter_m"],
                carrier_gas=row["carrier_gas"],
                layered_wall=parameters.layered_wall,
                campaign=row["campaign"],
                materials=catalog,
            )
            for row in _continuum_sweeps(setpoints)
        ],
        "pressure_bands_without_flue_state": _bare_pressure_bands(setpoints),
        "in_situ_bootstrap_cost_per_kg": parameters.in_situ_bootstrap_cost_per_kg,
    }


def interface_temperature_table(
    layered_wall: Mapping[str, Any],
    bands_C: Mapping[str, tuple[float, float]],
    *,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Hot-face band against cited service, and interfaces when k exists."""

    wall = normalize_layered_wall(layered_wall)
    catalog = _catalog(materials)
    rows: dict[str, Any] = {}
    duct_band = bands_C[HOT_DUCT_TRAIN_KEY]
    rows["hot_duct"] = _interface_row(
        "hot_duct",
        HOT_DUCT_TRAIN_KEY,
        duct_band,
        wall,
        product=None,
        materials=catalog,
    )
    for area_key, _number, train_key in COLLECTOR_STAGE_JOIN:
        product = None
        if area_key == _STAGE3_AREA_KEY and _STAGE3_AREA_KEY in wall["stage_bulk"]:
            product = wall["stage_bulk"][_STAGE3_AREA_KEY]
        rows[area_key] = _interface_row(
            area_key,
            train_key,
            bands_C[train_key],
            wall,
            product=product,
            materials=catalog,
        )
    return rows


def reprice_stage3_cases(
    sio_area_m2_by_case: Mapping[str, float],
    cost_parameters: Any | None = None,
    *,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-price cases. Base areas come from setpoints. SiO areas are arguments.

    The screening areas are not stored here. Pass them from the report.
    """

    parameters = _cost_parameters(cost_parameters)
    areas = _setpoint_areas(_setpoints())
    catalog = _catalog(materials)
    cases: dict[str, Any] = {}
    for name, sio_area in sio_area_m2_by_case.items():
        case_areas = dict(areas)
        case_areas[_STAGE3_AREA_KEY] = _finite_number(
            sio_area, f"{name}.sio_stage3"
        )
        if case_areas[_STAGE3_AREA_KEY] < 0.0:
            raise ValueError(f"{name}.sio_stage3 must be non-negative")
        cases[str(name)] = price_collector_stages(
            case_areas,
            layered_wall=parameters.layered_wall,
            import_delivery_cost_per_kg=parameters.import_delivery_cost_per_kg,
            in_situ_bootstrap_cost_per_kg=parameters.in_situ_bootstrap_cost_per_kg,
            stage_wall_thickness_m=parameters.condenser_stage_wall_thickness_m,
            stage_bulk_density_kg_m3=parameters.condenser_stage_bulk_density_kg_m3,
            materials=catalog,
        )
    return cases


def _normalize_layers(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        extra = set(raw).difference(_ROLE_BY_KEY)
        if extra:
            raise ValueError(f"layered_wall layers has unsupported keys: {sorted(extra)}")
        missing = [key for key, _role in _LAYER_KEYS if key not in raw]
        if missing:
            raise ValueError(f"layered_wall layers missing {missing}")
        return [
            _normalize_layer(key, role, raw[key])
            for key, role in _LAYER_KEYS
        ]
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if len(raw) != len(_LAYER_KEYS):
            raise ValueError("layered_wall layers must be liner, felt, structure")
        normalized = [
            _normalize_layer(expected_key, expected_role, layer)
            for (expected_key, expected_role), layer in zip(_LAYER_KEYS, raw, strict=True)
        ]
        return normalized
    raise TypeError("layered_wall layers must be a mapping or a list")


def _normalize_layer(key: str, role: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"layered_wall layer {key} must be a mapping")
    extra = set(raw).difference(_LAYER_FIELDS)
    if extra:
        raise ValueError(f"layered_wall.{key} has unsupported keys: {sorted(extra)}")
    declared = raw.get("role")
    if declared is not None and declared != role:
        raise ValueError(f"layered_wall.{key} role must be {role!r}")
    declared_key = raw.get("key")
    if declared_key is not None and declared_key != key:
        raise ValueError(f"layered_wall.{key} key must be {key!r}")
    material_id = raw.get("material_id")
    if material_id is not None and (
        not isinstance(material_id, str) or not material_id.strip()
    ):
        raise ValueError(f"layered_wall.{key} material_id must be a non-empty string or null")
    source = _optional_source(raw.get("source"), f"layered_wall.{key}.source")
    thickness = raw.get("thickness_m")
    if thickness is not None:
        thickness = _finite_number(thickness, f"layered_wall.{key}.thickness_m")
        if thickness <= 0.0:
            raise ValueError(f"layered_wall.{key}.thickness_m must be positive")
    density, density_status, density_citation = _optional_property(
        raw,
        value_key="density_kg_m3",
        status_key="density_status",
        citation_key="density_citation",
        where=f"layered_wall.{key}",
    )
    conductivity, conductivity_status, conductivity_citation = _optional_property(
        raw,
        value_key="conductivity_W_m_K",
        status_key="conductivity_status",
        citation_key="conductivity_citation",
        where=f"layered_wall.{key}",
    )
    curve = _optional_curve(raw.get("thermal_conductivity"), f"layered_wall.{key}")
    if conductivity is not None and curve is not None:
        raise ValueError(f"layered_wall.{key} conductivity_conflict")
    intended = raw.get("intended_material")
    if intended is not None and not isinstance(intended, str):
        raise ValueError(f"layered_wall.{key} intended_material must be a string")
    catalog_status = raw.get("catalog_status")
    if catalog_status is not None and not isinstance(catalog_status, str):
        raise ValueError(f"layered_wall.{key} catalog_status must be a string")
    return {
        "key": key,
        "role": role,
        "material_id": material_id,
        "source": source,
        "thickness_m": thickness,
        "intended_material": intended,
        "catalog_status": catalog_status,
        "density_kg_m3": density,
        "density_status": density_status,
        "density_citation": density_citation,
        "conductivity_W_m_K": conductivity,
        "conductivity_status": conductivity_status,
        "conductivity_citation": conductivity_citation,
        "thermal_conductivity": curve,
    }


def _normalize_stage_bulk(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError("layered_wall stage_bulk must be a mapping")
    extra = set(str(key) for key in raw).difference({_STAGE3_AREA_KEY})
    if extra:
        raise ValueError(f"layered_wall stage_bulk has unsupported keys: {sorted(extra)}")
    if _STAGE3_AREA_KEY not in raw:
        return {}
    entry = raw[_STAGE3_AREA_KEY]
    if not isinstance(entry, Mapping):
        raise TypeError("layered_wall stage_bulk.sio_stage3 must be a mapping")
    unknown = set(entry).difference(_STAGE_BULK_FIELDS)
    if unknown:
        raise ValueError(
            "layered_wall stage_bulk.sio_stage3 has unsupported keys: "
            f"{sorted(unknown)}"
        )
    if entry.get("kind") != "in_situ_product_bulk":
        raise ValueError("sio_stage3 kind must be in_situ_product_bulk")
    if entry.get("source") != "in_situ":
        raise ValueError("sio_stage3 source must be in_situ")
    material_id = entry.get("material_id")
    if not isinstance(material_id, str) or not material_id.strip():
        raise ValueError("sio_stage3 material_id must be a non-empty string")
    return {
        _STAGE3_AREA_KEY: {
            "kind": "in_situ_product_bulk",
            "material_id": material_id,
            "source": "in_situ",
        }
    }


def _optional_source(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if value not in _SOURCES:
        raise ValueError(f"{name} must be imported, in_situ, or null")
    return str(value)


def _optional_property(
    raw: Mapping[str, Any],
    *,
    value_key: str,
    status_key: str,
    citation_key: str,
    where: str,
) -> tuple[float | None, str | None, str | None]:
    value = raw.get(value_key)
    status = raw.get(status_key)
    citation = raw.get(citation_key)
    if value is None:
        if status is not None or citation is not None:
            raise ValueError(f"{where} {status_key} requires {value_key}")
        return None, None, None
    number = _finite_number(value, f"{where}.{value_key}")
    if number <= 0.0:
        raise ValueError(f"{where}.{value_key} must be positive")
    if status not in _PROPERTY_STATUSES:
        raise ValueError(f"{where}.{status_key} must be assumed or cited")
    if status == "cited":
        if not isinstance(citation, str) or not citation.strip():
            raise ValueError(f"{where}.{citation_key} is required when status is cited")
    elif citation is not None:
        raise ValueError(f"{where}.{citation_key} is only set when status is cited")
    return number, str(status), citation if isinstance(citation, str) else None


def _optional_curve(raw: Any, where: str) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise TypeError(f"{where}.thermal_conductivity must be a list")
    if not raw:
        raise ValueError(f"{where}.thermal_conductivity must not be empty")
    curve: list[dict[str, Any]] = []
    for index, point in enumerate(raw):
        if not isinstance(point, Mapping):
            raise TypeError(f"{where}.thermal_conductivity[{index}] must be a mapping")
        extra = set(point).difference(_CURVE_FIELDS)
        if extra or set(point) != _CURVE_FIELDS:
            raise ValueError(
                f"{where}.thermal_conductivity[{index}] must define "
                "temperature_K, value_W_m_K, and citation"
            )
        temperature = _finite_number(
            point["temperature_K"],
            f"{where}.thermal_conductivity[{index}].temperature_K",
        )
        value = _finite_number(
            point["value_W_m_K"],
            f"{where}.thermal_conductivity[{index}].value_W_m_K",
        )
        if value <= 0.0:
            raise ValueError(f"{where}.thermal_conductivity[{index}] must be positive")
        citation = point["citation"]
        if not isinstance(citation, str) or not citation.strip():
            raise ValueError(f"{where}.thermal_conductivity[{index}] citation is required")
        curve.append({
            "temperature_K": temperature,
            "value_W_m_K": value,
            "citation": citation,
        })
    return curve


def _price_product_bulk(
    area_key: str,
    area_m2: float,
    *,
    wall: Mapping[str, Any],
    thickness_m: float,
    density_kg_m3: float | None,
    import_delivery_cost_per_kg: float,
    in_situ_bootstrap_cost_per_kg: float | None,
    materials: Mapping[str, Any],
) -> dict[str, Any]:
    bulk = wall["stage_bulk"][area_key]
    if area_m2 == 0.0:
        row = _zero_area_row(area_key, "in_situ_product_bulk")
        row["failure_flags"] = product_surface_flags(
            bulk["material_id"], materials=materials
        )
        return row
    reasons: list[str] = []
    entry = materials.get(bulk["material_id"])
    if not isinstance(entry, Mapping):
        reasons.append(f"unknown_material:{bulk['material_id']}")
    if density_kg_m3 is None:
        reasons.append(f"density_missing:{area_key}")
    if reasons:
        unavailable = _unavailable(_stage_reason(area_key, reasons), "USD")
        return {
            "area_m2": area_m2,
            "pricing": "in_situ_product_bulk",
            "material_id": bulk["material_id"],
            "source": "in_situ",
            "cost_usd": unavailable,
            "mass_kg": _unavailable(_join(reasons), "kg"),
            "imported_kg": 0.0,
            "in_situ_kg": _unavailable(_join(reasons), "kg"),
            "imported_usd": 0.0,
            "in_situ_usd": unavailable,
            "failure_flags": product_surface_flags(
                bulk["material_id"], materials=materials
            ),
            "reasons": reasons,
        }
    # Stage-3 mass uses the existing cost-parameter slots. Those slots are
    # flagged provisional handbook values, not a cited wall_materials density.
    mass_kg = area_m2 * thickness_m * float(density_kg_m3)
    in_situ_usd = _price_known_mass(
        mass_kg,
        in_situ_bootstrap_cost_per_kg,
        "in_situ_cost_unset",
    )
    cost = in_situ_usd if not _is_unavailable(in_situ_usd) else _unavailable(
        _stage_reason(area_key, [_reason_of(in_situ_usd)]),
        "USD",
    )
    return {
        "area_m2": area_m2,
        "pricing": "in_situ_product_bulk",
        "geometry": "flat_sheet",
        "geometry_flag": "collector_area_priced_as_flat_sheet",
        "material_id": bulk["material_id"],
        "source": "in_situ",
        "wall_thickness_m": thickness_m,
        "wall_thickness_provenance": "condenser_stage_wall_thickness_m",
        "wall_thickness_certification": "uncertified_cost_parameter_slot",
        "bulk_density_kg_m3": float(density_kg_m3),
        "bulk_density_provenance": f"condenser_stage_bulk_density_kg_m3.{area_key}",
        "bulk_density_certification": "uncertified_cost_parameter_slot",
        "mass_kg": mass_kg,
        "imported_kg": 0.0,
        "in_situ_kg": mass_kg,
        "imported_usd": 0.0,
        "in_situ_usd": in_situ_usd,
        "cost_usd": cost,
        "failure_flags": product_surface_flags(
            bulk["material_id"], materials=materials
        ),
        "reasons": [] if not _is_unavailable(cost) else [_reason_of(cost)],
    }


def _price_layered_sheet(
    area_key: str,
    area_m2: float,
    *,
    wall: Mapping[str, Any],
    import_delivery_cost_per_kg: float,
    in_situ_bootstrap_cost_per_kg: float | None,
    materials: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = [
        resolve_layer_properties(layer, materials=materials) for layer in wall["layers"]
    ]
    flags = stack_failure_flags(resolved, materials=materials)
    if area_m2 == 0.0:
        row = _zero_area_row(area_key, "layered_stack")
        row["failure_flags"] = flags
        row["layers"] = [_layer_mass_row(layer, 0.0, None) for layer in resolved]
        return row
    layer_rows = []
    for layer in resolved:
        reasons = _layer_mass_reasons(layer)
        mass = None if reasons else area_m2 * layer["thickness_m"] * layer["density_kg_m3"]
        layer_rows.append(_layer_mass_row(layer, mass, reasons))
    split = _source_split(
        layer_rows,
        import_delivery_cost_per_kg=import_delivery_cost_per_kg,
        in_situ_bootstrap_cost_per_kg=in_situ_bootstrap_cost_per_kg,
    )
    cost = split["cost_usd"]
    if _is_unavailable(cost):
        cost = _unavailable(
            _stage_reason(area_key, _reason_of(cost).split(";")),
            "USD",
        )
    return {
        "area_m2": area_m2,
        "pricing": "layered_stack",
        "geometry": "flat_sheet",
        "geometry_flag": "collector_area_priced_as_flat_sheet",
        "layers": layer_rows,
        "failure_flags": flags,
        "mass_kg": split["mass_kg"],
        "imported_kg": split["imported_kg"],
        "in_situ_kg": split["in_situ_kg"],
        "imported_usd": split["imported_usd"],
        "in_situ_usd": split["in_situ_usd"],
        "cost_usd": cost,
        "reasons": [] if not _is_unavailable(cost) else [_reason_of(cost)],
    }


def _layer_mass_reasons(layer: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    role = layer["role"]
    unknown = layer.get("unknown_reason")
    if unknown and (
        str(unknown).startswith("unknown_material:")
        or layer.get("density_kg_m3") is None
    ):
        reasons.append(str(unknown))
    if layer.get("thickness_m") is None:
        reasons.append(f"thickness_unset:{role}")
    if layer.get("density_kg_m3") is None:
        reasons.append(layer.get("density_reason") or f"density_missing:{role}")
    if layer.get("source") is None:
        reasons.append(f"source_unset:{role}")
    return reasons


def _layer_mass_row(
    layer: Mapping[str, Any],
    mass_kg: float | None,
    reasons: list[str] | None,
) -> dict[str, Any]:
    reason = _join(reasons or [])
    return {
        "role": layer["role"],
        "material_id": layer.get("material_id"),
        "source": layer.get("source"),
        "thickness_m": layer.get("thickness_m"),
        "density_kg_m3": layer.get("density_kg_m3"),
        "density_flag": layer.get("density_flag"),
        "mass_kg": mass_kg if not reason else _unavailable(reason, "kg"),
        "mass_reason": reason or None,
        "reasons": list(reasons or []),
    }


def _cylindrical_masses(
    layers: Sequence[Mapping[str, Any]],
    radius_m: float,
    length_m: float,
) -> tuple[list[dict[str, Any]], float | None]:
    """Concentric shell masses. An unset inner thickness blocks every outer shell.

    Premise: each layer is a right circular tube of length L.
    Algebra: V = π (r_out² - r_in²) L, and mass = V × density.
    Units: radii and L in m, density in kg/m³, mass in kg.
    Sanity limit: a thin shell approaches (2 π r_in L) t density, the
    unrolled flat sheet. A non-positive gas-path radius is refused by
    the caller.
    """

    rows: list[dict[str, Any]] = []
    cursor = radius_m
    blocked = False
    for layer in layers:
        reasons = _layer_mass_reasons(layer)
        if blocked:
            reasons.append(f"inner_thickness_unset:{layer['role']}")
        elif layer.get("thickness_m") is None:
            blocked = True
        mass = None
        if not reasons and layer.get("thickness_m") is not None:
            outer = cursor + layer["thickness_m"]
            volume = math.pi * (outer ** 2 - cursor ** 2) * length_m
            mass = volume * layer["density_kg_m3"]
            cursor = outer
        rows.append(_layer_mass_row(layer, mass, reasons))
    outer_radius = None if blocked else cursor
    return rows, outer_radius


def _source_split(
    layers: Sequence[Mapping[str, Any]],
    *,
    import_delivery_cost_per_kg: float,
    in_situ_bootstrap_cost_per_kg: float | None,
) -> dict[str, Any]:
    imported_kg, imported_reasons = _bucket(layers, "imported")
    in_situ_kg, in_situ_reasons = _bucket(layers, "in_situ")
    unset_reasons = [
        f"source_unset:{layer['role']}"
        for layer in layers
        if layer.get("source") is None
    ]
    imported_usd = _price_bucket(
        imported_kg, imported_reasons, import_delivery_cost_per_kg, None
    )
    in_situ_usd = _price_bucket(
        in_situ_kg,
        in_situ_reasons,
        in_situ_bootstrap_cost_per_kg,
        "in_situ_cost_unset",
    )
    gap_reasons = [reason for layer in layers for reason in layer.get("reasons", [])]
    gap_reasons.extend(unset_reasons)
    if _is_unavailable(imported_usd):
        gap_reasons.append(_reason_of(imported_usd))
    if _is_unavailable(in_situ_usd):
        gap_reasons.append(_reason_of(in_situ_usd))
    if gap_reasons:
        total_mass = _unavailable(_join(gap_reasons), "kg")
        total_usd = _unavailable(_join(gap_reasons), "USD")
    else:
        total_mass = imported_kg + in_situ_kg
        total_usd = imported_usd + in_situ_usd
    return {
        "imported_kg": imported_kg if not imported_reasons else _unavailable(_join(imported_reasons), "kg"),
        "in_situ_kg": in_situ_kg if not in_situ_reasons else _unavailable(_join(in_situ_reasons), "kg"),
        "imported_usd": imported_usd,
        "in_situ_usd": in_situ_usd,
        "mass_kg": total_mass,
        "cost_usd": total_usd,
    }


def _bucket(
    layers: Sequence[Mapping[str, Any]],
    source: str,
) -> tuple[float, list[str]]:
    total = 0.0
    reasons: list[str] = []
    for layer in layers:
        if layer.get("source") != source:
            continue
        mass = layer.get("mass_kg")
        if _is_unavailable(mass):
            reasons.append(_reason_of(mass))
        else:
            total += float(mass)
    return total, reasons


def _price_bucket(
    mass_kg: float,
    reasons: list[str],
    rate: float | None,
    unset_reason: str | None,
) -> Any:
    if reasons:
        return _unavailable(_join(reasons), "USD")
    return _price_known_mass(mass_kg, rate, unset_reason)


def _price_known_mass(
    mass_kg: float,
    rate: float | None,
    unset_reason: str | None,
) -> Any:
    if mass_kg == 0.0:
        return 0.0
    if rate is None:
        return _unavailable(unset_reason or "cost_rate_unset", "USD")
    return mass_kg * rate


def _ready_conduction_layers(
    layers: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, float | str]], list[str]]:
    ready: list[dict[str, float | str]] = []
    reasons: list[str] = []
    for layer in layers:
        role = str(layer.get("role") or layer.get("key") or "layer")
        thickness = layer.get("thickness_m")
        conductivity = layer.get("conductivity_W_m_K")
        layer_ok = True
        if thickness is None:
            reasons.append(f"thickness_unset:{role}")
            layer_ok = False
        if conductivity is None:
            reasons.append(
                str(layer.get("conductivity_reason") or f"thermal_conductivity_missing:{role}")
            )
            layer_ok = False
        if layer_ok:
            thickness_m = float(thickness)
            conductivity_w = float(conductivity)
            if thickness_m <= 0.0:
                reasons.append(f"thickness_invalid:{role}")
            elif conductivity_w <= 0.0:
                reasons.append(f"thermal_conductivity_invalid:{role}")
            else:
                ready.append({
                    "role": role,
                    "thickness_m": thickness_m,
                    "conductivity_W_m_K": conductivity_w,
                })
    if reasons:
        return [], reasons
    return ready, []


def _temperature_walk(
    layers: Sequence[Mapping[str, Any]],
    resistances: Sequence[float],
    t_in: float,
    flow: float,
    t_out: float,
) -> list[dict[str, Any]]:
    temperature = t_in
    walk = [{"at": "hot_face", "temperature_C": temperature}]
    for layer, resistance in zip(layers, resistances, strict=True):
        temperature = temperature - flow * resistance
        walk.append({
            "at": f"after:{layer['role']}",
            "temperature_C": temperature,
        })
    span = max(1.0, abs(t_in - t_out))
    if abs(temperature - t_out) > 1e-9 * span:
        raise RuntimeError("conduction temperature walk did not close")
    walk[-1]["temperature_C"] = t_out
    return walk


def _resolve_density(
    layer: Mapping[str, Any],
    entry: Mapping[str, Any] | None,
) -> tuple[float | None, str | None, str | None]:
    role = str(layer["role"])
    override = layer.get("density_kg_m3")
    catalog_value = entry.get("density_kg_m3") if isinstance(entry, Mapping) else None
    if catalog_value is not None and override is not None:
        if float(catalog_value) != float(override):
            raise ValueError(f"density_conflict:{role}")
    if override is not None:
        return float(override), None, str(layer.get("density_status"))
    if catalog_value is not None:
        return float(catalog_value), None, "catalog"
    return None, f"density_missing:{role}", None


def _resolve_conductivity(
    layer: Mapping[str, Any],
    entry: Mapping[str, Any] | None,
    *,
    temperature_K: float | None,
) -> tuple[float | None, str | None, str | None]:
    role = str(layer["role"])
    override_scalar = layer.get("conductivity_W_m_K")
    override_curve = layer.get("thermal_conductivity")
    catalog_scalar = None
    catalog_curve = None
    if isinstance(entry, Mapping):
        catalog_scalar = entry.get("conductivity_W_m_K")
        if catalog_scalar is None:
            catalog_scalar = entry.get("thermal_conductivity_W_m_K")
        catalog_curve = entry.get("thermal_conductivity")
    if override_scalar is not None and catalog_scalar is not None:
        if float(override_scalar) != float(catalog_scalar):
            raise ValueError(f"conductivity_conflict:{role}")
    if override_curve is not None:
        return _select_curve(override_curve, temperature_K, role)
    if isinstance(catalog_curve, Sequence) and not isinstance(catalog_curve, (str, bytes)):
        return _select_curve(catalog_curve, temperature_K, role)
    if override_scalar is not None:
        return float(override_scalar), None, "k_not_a_curve"
    if catalog_scalar is not None:
        return float(catalog_scalar), None, "k_not_a_curve"
    return None, f"thermal_conductivity_missing:{role}", None


def _select_curve(
    curve: Sequence[Any],
    temperature_K: float | None,
    role: str,
) -> tuple[float | None, str | None, str | None]:
    if temperature_K is None:
        return None, f"thermal_conductivity_temperature_unset:{role}", None
    for point in curve:
        if float(point["temperature_K"]) == float(temperature_K):
            return float(point["value_W_m_K"]), None, "cited_grid"
    return None, f"not_on_cited_grid:{role}", None


def _service_record(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    service = entry.get("service_temp") if isinstance(entry, Mapping) else None
    if not isinstance(service, Mapping):
        return {
            "continuous_C": None,
            "max_operating_C": None,
            "peak_C": None,
            "degradation_onset_C": None,
            "citations": [],
            "evidence": None,
        }
    citations = service.get("citations") or []
    return {
        "continuous_C": service.get("continuous_C"),
        "max_operating_C": service.get("max_operating_C"),
        "peak_C": service.get("peak_C"),
        "degradation_onset_C": service.get("degradation_onset_C"),
        "citations": [str(item) for item in citations],
        "evidence": service.get("evidence"),
    }


def _gas_tight_record(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        return {"certified": False, "gas_tight": None, "permeability_m2": None}
    has_tight = "gas_tight" in entry
    has_permeability = "permeability_m2" in entry
    return {
        "certified": has_tight or has_permeability,
        "gas_tight": entry.get("gas_tight") if has_tight else None,
        "permeability_m2": entry.get("permeability_m2") if has_permeability else None,
    }


def _sio_attack(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    attack = entry.get("chemical_attack")
    if not isinstance(attack, Mapping):
        return None
    sio = attack.get("SiO")
    if not isinstance(sio, Mapping):
        return None
    citations = sio.get("citations") or []
    return {
        "severity": sio.get("severity"),
        "evidence": sio.get("evidence"),
        "citations": [str(item) for item in citations],
        "note": sio.get("note"),
    }


def _gas_tight_flags(
    facing: Mapping[str, Any] | None,
    *,
    product_surface: bool,
) -> list[dict[str, Any]]:
    code = (
        "product_surface_gas_tightness_uncertified"
        if product_surface
        else "liner_gas_tightness_uncertified"
    )
    record = facing.get("gas_tight") if isinstance(facing, Mapping) else None
    if not isinstance(record, Mapping) or not record.get("certified"):
        return [{
            "code": code,
            "status": "flagged",
            "detail": (
                "The wall catalog has no gas_tight or permeability_m2 value "
                "for the gas-facing surface."
            ),
            "citations": [],
        }]
    if record.get("gas_tight") is False:
        return [{
            "code": "liner_not_gas_tight",
            "status": "flagged",
            "detail": "The catalog marks the gas-facing surface as not gas-tight.",
            "citations": [],
        }]
    return []


def _cao_attack_flag(structure: Mapping[str, Any] | None) -> dict[str, Any]:
    attack = structure.get("chemical_attack_sio") if isinstance(structure, Mapping) else None
    if not isinstance(attack, Mapping):
        return {
            "code": "cao_sio2_attack_evidence_missing",
            "status": "flagged",
            "detail": "The CaO-bearing shell has no catalog SiO attack record.",
            "citations": [],
        }
    return {
        "code": "cao_sio2_attack_if_liner_breached",
        "status": "flagged",
        "severity": attack.get("severity"),
        "evidence": attack.get("evidence"),
        "detail": attack.get("note"),
        "citations": list(attack.get("citations") or []),
    }


def _recession_flags(
    gas_facing: Mapping[str, Any] | None,
    *,
    structure_is_cao: bool,
) -> list[dict[str, Any]]:
    if _is_cao_bearing(gas_facing):
        return [{
            "code": "cao_vacuum_recession_applies",
            "status": "flagged",
            "detail": (
                "CaO-bearing material is on the gas path. The t-476 "
                "hot-vacuum dissociative recession screen applies. Doloma is "
                "CaO.MgO, not the pure-CaO furnace row, and no rate is computed."
            ),
            "citations": ["t-476"],
        }]
    if structure_is_cao:
        return [{
            "code": "cao_vacuum_recession_screen_not_applied",
            "status": "flagged",
            "reason": "shell_not_gas_facing",
            "detail": (
                "The CaO-bearing shell is not the gas-facing layer. The t-476 "
                "screen applies only where that CaO is exposed."
            ),
            "citations": ["t-476"],
        }]
    return [{
        "code": "cao_vacuum_recession_not_on_gas_path",
        "status": "not_applicable",
        "reason": "gas_facing_is_not_cao",
        "detail": "The gas-facing material is not a CaO-bearing wall id.",
        "citations": ["t-476"],
    }]


def _is_cao_bearing(layer: Mapping[str, Any] | None) -> bool:
    if not isinstance(layer, Mapping):
        return False
    return layer.get("material_id") in _CAO_BEARING_MATERIAL_IDS


def _interface_row(
    name: str,
    train_key: str,
    band: tuple[float, float],
    wall: Mapping[str, Any],
    *,
    product: Mapping[str, Any] | None,
    materials: Mapping[str, Any],
) -> dict[str, Any]:
    hot_face = float(band[1])
    if product is not None:
        entry = materials.get(product["material_id"])
        service = _service_record(entry if isinstance(entry, Mapping) else None)
        return {
            "stage": name,
            "train_key": train_key,
            "band_C": [float(band[0]), hot_face],
            "hot_face_C": hot_face,
            "hot_face_basis": "collector_band_maximum_not_measured_gas_temperature",
            "pricing": "in_situ_product_bulk",
            "layers": [{
                "role": "product_surface",
                "material_id": product["material_id"],
                "service": service,
                "hot_face_flags": _service_flags(hot_face, service),
                "inner_temperature_C": _unavailable(
                    "single_baffle_no_layer_interface", "C"
                ),
                "outer_temperature_C": _unavailable(
                    "outer_temperature_unset"
                    if wall["outer_temperature_C"] is None
                    else "thermal_conductivity_missing:product_surface",
                    "C",
                ),
            }],
            "shell_temperature_status": "not_a_cao_shell",
            "conduction_status": "unavailable",
            "conduction_reason": "product_baffle_has_no_cited_conductivity",
            "failure_flags": product_surface_flags(
                product["material_id"], materials=materials
            ),
        }
    evaluated = [
        resolve_layer_properties(
            layer,
            materials=materials,
            temperature_K=hot_face + CELSIUS_TO_KELVIN_OFFSET,
        )
        for layer in wall["layers"]
    ]
    _ready, layer_reasons = _ready_conduction_layers(evaluated)
    conduction_reasons = list(layer_reasons)
    if wall["outer_temperature_C"] is None:
        conduction_reasons.append("outer_temperature_unset")
    if conduction_reasons:
        conduction = _conduction_unavailable(conduction_reasons)
    else:
        conduction = flat_wall_conduction(
            evaluated,
            hot_face,
            wall["outer_temperature_C"],
        )
    layer_rows = []
    interfaces = (
        conduction["interface_temperatures_C"] if conduction["status"] == "ok" else None
    )
    for index, layer in enumerate(evaluated):
        service = layer["service"]
        if interfaces is not None:
            inner = interfaces[index]["temperature_C"]
            outer = interfaces[index + 1]["temperature_C"]
            temperature_flags = _service_flags(inner, service)
        else:
            inner = _unavailable(_reason_of(conduction), "C")
            outer = _unavailable(_reason_of(conduction), "C")
            temperature_flags = (
                _service_flags(hot_face, service)
                if index == 0
                else ["layer_temperature_unchecked"]
            )
        layer_rows.append({
            "role": layer["role"],
            "material_id": layer.get("material_id"),
            "source": layer.get("source"),
            "thickness_m": layer.get("thickness_m"),
            "density_kg_m3": layer.get("density_kg_m3"),
            "density_reason": layer.get("density_reason"),
            "conductivity_W_m_K": layer.get("conductivity_W_m_K"),
            "conductivity_reason": layer.get("conductivity_reason"),
            "conductivity_flag": layer.get("conductivity_flag"),
            "service": service,
            "hot_face_flags": temperature_flags,
            "inner_temperature_C": inner,
            "outer_temperature_C": outer,
        })
    structure = next(layer for layer in evaluated if layer["role"] == "structure")
    shell_status = "shell_temperature_unchecked"
    if interfaces is not None and _is_cao_bearing(structure):
        structure_index = next(
            index for index, layer in enumerate(evaluated) if layer["role"] == "structure"
        )
        inner = interfaces[structure_index]["temperature_C"]
        shell_flags = _service_flags(inner, structure["service"])
        if "hot_face_above_max_operating" in shell_flags:
            shell_status = "shell_above_max_operating"
        elif "service_temperature_missing" in shell_flags:
            shell_status = "structure_service_temperature_unchecked"
        else:
            shell_status = "shell_within_cited_service"
    return {
        "stage": name,
        "train_key": train_key,
        "band_C": [float(band[0]), hot_face],
        "hot_face_C": hot_face,
        "hot_face_basis": "collector_band_maximum_not_measured_gas_temperature",
        "pricing": "layered_stack",
        "layers": layer_rows,
        "shell_temperature_status": shell_status,
        "conduction_status": conduction["status"],
        "conduction_reason": (
            None if conduction["status"] == "ok" else _reason_of(conduction)
        ),
        "failure_flags": stack_failure_flags(evaluated),
    }


def _service_flags(temperature_C: float, service: Mapping[str, Any]) -> list[str]:
    continuous = service.get("continuous_C")
    maximum = service.get("max_operating_C")
    onset = service.get("degradation_onset_C")
    if continuous is None and maximum is None:
        return ["service_temperature_missing"]
    flags: list[str] = []
    if maximum is not None and temperature_C > float(maximum):
        flags.append("hot_face_above_max_operating")
    elif continuous is not None and temperature_C > float(continuous):
        flags.append("hot_face_above_continuous_service")
    else:
        flags.append("within_continuous_service")
    if maximum is not None and temperature_C == float(maximum):
        flags.append("hot_face_at_max_operating")
    if onset is not None and temperature_C > float(onset):
        flags.append("hot_face_above_degradation_onset")
    return flags


def _n2_cp_bounds(temperature_K: float) -> dict[str, Any]:
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document
    from simulator.transport_constants import CARRIER_GAS_PROPERTIES

    document = load_table_document(TABLES_DIR / f"{_N2_TABLE_ID}.yaml")
    table = document["table"]
    if table.get("table_id") != _N2_TABLE_ID:
        raise ValueError("N-023 table_id mismatch")
    phase = table.get("index_entry", {}).get("state")
    if phase != "ref":
        return _unavailable(f"n2_table_phase_unexpected:{phase}", "J/(mol·K)")
    rows = []
    for row in table["values"]:
        heat = row.get("heat_capacity")
        temperature = row.get("temperature")
        if not isinstance(heat, Mapping) or not isinstance(temperature, Mapping):
            continue
        if heat.get("value") is None or heat.get("as_published") == "INFINITE":
            continue
        rows.append((
            float(temperature["value"]),
            float(heat["value"]),
            heat.get("locator"),
        ))
    below = None
    above = None
    exact = None
    for printed_t, cp, locator in rows:
        if printed_t == temperature_K:
            exact = (printed_t, cp, locator)
        elif printed_t < temperature_K and (below is None or printed_t > below[0]):
            below = (printed_t, cp, locator)
        elif printed_t > temperature_K and (above is None or printed_t < above[0]):
            above = (printed_t, cp, locator)
    molar_mass = CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol
    payload = {
        "table_id": _N2_TABLE_ID,
        "phase": phase,
        "molar_mass_kg_mol": molar_mass,
        "molar_mass_owner": "simulator.transport_constants.CARRIER_GAS_PROPERTIES",
        "gas_temperature_K": temperature_K,
        "cp_temperature_status": (
            "on_printed_grid"
            if exact is not None
            else "cp_temperature_is_printed_neighbor_not_the_gas_temperature"
        ),
    }
    if exact is not None:
        payload["exact"] = _cp_point(*exact)
        return payload
    if below is None or above is None:
        return _unavailable("cp_bracket_missing", "J/(mol·K)")
    payload["below"] = _cp_point(*below)
    payload["above"] = _cp_point(*above)
    return payload


def _cp_point(temperature_K: float, cp: float, locator: Any) -> dict[str, Any]:
    return {
        "temperature_K": temperature_K,
        "cp_J_per_mol_K": cp,
        "locator": locator,
    }


def _ideal_gas_density_kg_m3(pressure_Pa: float, temperature_K: float) -> float:
    """Ideal-gas mass density of the N2 carrier.

    Premise: at a few millibar the carrier is an ideal gas. Algebra:
    rho = P M / (R T). Units: Pa × kg/mol / (J/(mol·K) × K) = kg/m³,
    because Pa = J/m³. Sanity limit: density doubles when pressure doubles
    and falls as temperature rises. Non-positive inputs are refused by
    the caller.
    """

    from simulator.transport_constants import CARRIER_GAS_PROPERTIES

    molar_mass = CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol
    return pressure_Pa * molar_mass / (GAS_CONSTANT * temperature_K)


def _volumetric_heat(densities: Mapping[str, float], cp_bounds: Mapping[str, Any]) -> Any:
    if _is_unavailable(cp_bounds) or "below" not in cp_bounds and "exact" not in cp_bounds:
        return _unavailable("cp_bounds_unavailable", "J/(m3·K)")
    points = []
    if "exact" in cp_bounds:
        points = [cp_bounds["exact"]]
    else:
        points = [cp_bounds["below"], cp_bounds["above"]]
    molar_mass = cp_bounds["molar_mass_kg_mol"]
    return {
        "status": cp_bounds["cp_temperature_status"],
        "by_pressure": {
            name: [
                {
                    "using_temperature_K": point["temperature_K"],
                    "J_per_m3_K": density * point["cp_J_per_mol_K"] / molar_mass,
                }
                for point in points
            ]
            for name, density in densities.items()
        },
    }


def _inventory_heat(volumetric: Any, radius_m: float) -> Any:
    if _is_unavailable(volumetric):
        return _unavailable("cp_bounds_unavailable", "J/(m·K)")
    # Volume of one metre of gas path. This is an inventory, not a flow.
    volume_per_m = math.pi * radius_m ** 2
    rows = {}
    for name, points in volumetric["by_pressure"].items():
        rows[name] = [
            {
                "using_temperature_K": point["using_temperature_K"],
                "J_per_m_K": point["J_per_m3_K"] * volume_per_m,
            }
            for point in points
        ]
    return {
        "status": "inventory_not_a_stream_power",
        "by_pressure": rows,
    }


def _continuum_sweeps(setpoints: Mapping[str, Any]) -> list[dict[str, Any]]:
    campaigns = setpoints.get("campaigns")
    if not isinstance(campaigns, Mapping):
        raise ValueError("setpoints campaigns missing")
    rows = []
    for name, campaign in campaigns.items():
        if not isinstance(campaign, Mapping):
            continue
        bounds = campaign.get("continuum_pressure_bounds")
        if not isinstance(bounds, Mapping):
            continue
        pressures = bounds.get("pN2_mbar")
        if (
            not isinstance(pressures, Sequence)
            or isinstance(pressures, (str, bytes))
            or len(pressures) != 2
        ):
            raise ValueError(f"{name} continuum pressure bounds are invalid")
        rows.append({
            "campaign": str(name),
            "carrier_gas": str(bounds.get("carrier_gas")),
            "gas_temperature_C": bounds.get("gas_temperature_C"),
            "pipe_diameter_m": bounds.get("pipe_diameter_m"),
            "pressure_mbar": [float(pressures[0]), float(pressures[1])],
        })
    return rows


def _bare_pressure_bands(setpoints: Mapping[str, Any]) -> list[dict[str, Any]]:
    campaigns = setpoints.get("campaigns")
    rows = []
    if not isinstance(campaigns, Mapping):
        return rows
    for name, campaign in campaigns.items():
        if not isinstance(campaign, Mapping):
            continue
        if isinstance(campaign.get("continuum_pressure_bounds"), Mapping):
            continue
        pressures = campaign.get("p_total_mbar")
        if (
            isinstance(pressures, Sequence)
            and not isinstance(pressures, (str, bytes))
            and len(pressures) == 2
            and float(pressures[0]) == 5.0
            and float(pressures[1]) == 15.0
        ):
            rows.append({
                "campaign": str(name),
                "status": "unavailable",
                "reason": "flue_gas_temperature_unset",
                "carrier_gas": campaign.get("carrier_gas"),
                "p_total_mbar": [5.0, 15.0],
            })
    return rows


def _sweep_diameter(setpoints: Mapping[str, Any]) -> float:
    sweeps = _continuum_sweeps(setpoints)
    diameters = {row["pipe_diameter_m"] for row in sweeps}
    if len(diameters) != 1:
        raise ValueError("continuum sweeps do not share one pipe diameter")
    return float(next(iter(diameters)))


def _setpoint_areas(setpoints: Mapping[str, Any]) -> dict[str, float]:
    geometry = setpoints.get("condenser_geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError("setpoints condenser_geometry missing")
    raw = geometry.get("stage_areas_m2")
    if not isinstance(raw, Mapping):
        raise ValueError("setpoints stage_areas_m2 missing")
    areas = {}
    for key in _AREA_KEYS:
        if key not in raw:
            raise ValueError(f"setpoints stage area missing: {key}")
        areas[key] = _finite_number(raw[key], f"stage_areas_m2.{key}")
    return areas


def _collector_bands(setpoints: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    from simulator.config import resolve_collector_bands_C, resolve_substage_bands_C

    integer_bands = resolve_collector_bands_C(setpoints)
    substage_bands = resolve_substage_bands_C(setpoints)
    bands = {HOT_DUCT_TRAIN_KEY: integer_bands[0]}
    for _area_key, number, train_key in COLLECTOR_STAGE_JOIN:
        if number is None:
            bands[train_key] = substage_bands[train_key]
        else:
            bands[train_key] = integer_bands[number]
    return bands


def _setpoints() -> dict[str, Any]:
    from simulator.config import DEFAULT_DATA_DIR, _load_setpoints_file

    return _load_setpoints_file(DEFAULT_DATA_DIR / "setpoints.yaml")


def _cost_parameters(cost_parameters: Any | None) -> Any:
    if cost_parameters is None:
        from simulator.cost_parameters import cost_parameters_from_mapping

        return cost_parameters_from_mapping()
    return cost_parameters


def _catalog(materials: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if materials is not None:
        return materials
    from simulator.wall_advisor import load_wall_materials

    loaded = load_wall_materials()["materials"]
    if not isinstance(loaded, Mapping):
        raise ValueError("wall materials catalog is not a mapping")
    return loaded


def _validated_areas(stage_areas_m2: Mapping[str, Any]) -> dict[str, float] | None:
    if set(stage_areas_m2) != set(_AREA_KEYS):
        return None
    areas = {}
    for key in _AREA_KEYS:
        number = _finite_number(stage_areas_m2[key], f"stage_areas_m2.{key}")
        if number < 0.0:
            return None
        areas[key] = number
    return areas


def _area_problem(stage_areas_m2: Mapping[str, Any]) -> str:
    missing = [key for key in _AREA_KEYS if key not in stage_areas_m2]
    extra = [str(key) for key in stage_areas_m2 if str(key) not in _AREA_KEYS]
    reasons = [f"unknown_stage:{key}" for key in sorted(extra)]
    reasons.extend(f"collector_stage_area_missing:{key}" for key in missing)
    for key in _AREA_KEYS:
        if key not in stage_areas_m2:
            continue
        try:
            number = _finite_number(stage_areas_m2[key], key)
        except (TypeError, ValueError):
            reasons.append(f"collector_stage_area_invalid:{key}")
            continue
        if number < 0.0:
            reasons.append(f"collector_stage_area_invalid:{key}")
    return _join(reasons)


def _closed_summary(reason: str) -> dict[str, Any]:
    unavailable = _unavailable(reason, "USD")
    return {
        "status": "unavailable",
        "hardware_capital_cost_usd": unavailable,
        "collector_stage_cost_usd": unavailable,
        "reason": reason,
    }


def _duct_closed(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "cost_usd": _unavailable(reason, "USD"),
        "reason": reason,
    }


def _zero_area_row(area_key: str, pricing: str) -> dict[str, Any]:
    return {
        "area_m2": 0.0,
        "pricing": pricing,
        "stage": area_key,
        "mass_kg": 0.0,
        "imported_kg": 0.0,
        "in_situ_kg": 0.0,
        "imported_usd": 0.0,
        "in_situ_usd": 0.0,
        "cost_usd": 0.0,
        "reasons": [],
    }


def _sum_money(rows: Mapping[str, Mapping[str, Any]], reasons: list[str]) -> Any:
    if reasons:
        return _unavailable(_join(reasons), "USD")
    return sum(float(row["cost_usd"]) for row in rows.values())


def _sum_field(rows: Mapping[str, Mapping[str, Any]], field_name: str) -> Any:
    total = 0.0
    reasons: list[str] = []
    for row in rows.values():
        value = row[field_name]
        if _is_unavailable(value):
            reasons.append(_reason_of(value))
        else:
            total += float(value)
    if reasons:
        units = "USD" if field_name.endswith("usd") else "kg"
        return _unavailable(_join(reasons), units)
    return total


def _stage_reason(stage: str, reasons: list[str]) -> str:
    return f"collector_stage_cost_unavailable:{stage}:{_join(reasons)}"


def _conduction_unavailable(reasons: list[str]) -> dict[str, Any]:
    reason = _join(reasons)
    return {
        "status": "unavailable",
        "reason": reason,
        "heat_flux_W_m2": _unavailable(reason, "W/m2"),
        "heat_flow_W_per_m": _unavailable(reason, "W/m"),
    }


def _unavailable(reason: str, units: str) -> dict[str, Any]:
    from simulator.cost_energy import unavailable_quantity

    return unavailable_quantity(reason=reason, units=units)


def _is_unavailable(value: Any) -> bool:
    from simulator.cost_energy import is_unavailable_quantity

    return is_unavailable_quantity(value)


def _reason_of(value: Any) -> str:
    from simulator.cost_energy import unavailable_reason_of

    return unavailable_reason_of(value)


def _join(reasons: list[str]) -> str:
    ordered: list[str] = []
    for reason in reasons:
        if reason and reason not in ordered:
            ordered.append(reason)
    return ";".join(ordered)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise TypeError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number
