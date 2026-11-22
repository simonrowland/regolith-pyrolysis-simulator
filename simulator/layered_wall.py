"""Layered, source-aware collector and duct wall.

The owner design (2026-11-21) is a hot-face liner, a ceramic-felt middle,
and a CaO-based structural shell. Stage 3 is not that stack: its capture
surface is the fused-silica product, so its bulk is priced from the
existing stage thickness and density slots as in-situ silica.

A missing catalog number stays unavailable. A cited conductivity may be
interpolated inside its printed grid, or extended to the nearest printed
point when the catalog says so, and both cases are flagged. The in-situ
USD/kg is the JANAF sensible-enthalpy derivation when the cost entry asks
for it. An explicit null with status owner_unset is not refilled. Liner
material is an operator setting (t-472), read from the cost block, and is
not an optimizer knob.
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
    MBAR_PER_BAR,
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
    "source_status",
    "thickness_m",
    "thickness_status",
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
    "enclosure_temperature_C",
    "enclosure_temperature_status",
    "layers",
    "stage_bulk",
})
_STAGE_BULK_FIELDS = frozenset({
    "kind",
    "material_id",
    "source",
    "cost_per_kg_usd",
    "cost_status",
})
_CURVE_FIELDS = frozenset({"temperature_K", "value_W_m_K", "citation"})
_N2_TABLE_ID = "N-023"
_DERIVED_COST_STATUS = "derived_sensible_enthalpy"
_OWNER_UNSET_STATUS = "owner_unset"
_CONDUCTION_WALK_STATUSES = frozenset({"ok", "estimated_known_layers_only"})
# Printed-grid match. 1000 C + 273.15 and the literal 1273.15 can differ
# by a few ulps; this is a comparison tolerance, not a fabricated point.
_TEMPERATURE_MATCH_K = 1e-6
_DOLOMA_PROCESS_C = 1400.0
_DOLOMA_COMPONENTS = (("CaO", "Ca-027"), ("MgO", "Mg-008"))
_SILICA_COMPONENT = ("SiO2", "O-037")
_THERMAL_EFFICIENCY = 1.0
_EFFICIENCY_FLAG = "sensible_enthalpy_only_kiln_losses_not_cited"
_SIO_TABLE_ID = "O-012"
_SECONDS_PER_HOUR = 3600.0
# NASA TP-1190, Curt H. Liebert, June 1978. Printed sentence: the
# hemispherical total emittance at both the calculated temperature 1320 K
# and the gas-side surface 1356 K is 0.51 (fig. 11 or eq. (10) with
# coating thickness x = 0.020 cm). That surface is a thin YSZ
# thermal-barrier coating, not a doloma brick. It is the radiating-face
# stand-in while doloma conductivity and a doloma-shell emissivity are
# uncited. It is not an optimizer knob and it is not stored on the priced
# wall, so it does not enter the cost identity.
_YSZ_TBC_EMISSIVITY = 0.51
_YSZ_TBC_EMISSIVITY_CITATION = (
    "NASA TP-1190, Liebert, June 1978, hemispherical total emittance 0.51 "
    "at 1320 K and at a 1356 K gas-side surface, coating thickness 0.020 cm. "
    "YSZ thermal-barrier coating, not a doloma shell."
)
# t-1884 design run, probe label with_mn_stage_fixed_point.
# D452_PEAK SiO hour 107 incident_kg_hr, the peak the SiO area was sized
# to. The same hour's duct window (duct:stage_0_to_stage_1) prints p_SiO
# and pO2. Na and K are not in that print. Not a sum across hours.
_DESIGN_SIO_PEAK_KG_PER_H = 14.3763683197
_DESIGN_DUCT_SIO_PARTIAL_PA = 10.902171990335434
_DESIGN_DUCT_PO2_MBAR = 0.0
_DESIGN_POINT_LABEL = "with_mn_stage_fixed_point"
_DESIGN_POINT_HOUR = 107
_DESIGN_POINT_LOCATION = "duct:stage_0_to_stage_1"
_FELT_LIMIT_THICKNESS_CAP_M = 5.0
_VAPOUR_CATALOG: Any = None


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
    enclosure = raw.get("enclosure_temperature_C")
    if enclosure is not None:
        enclosure = _finite_number(enclosure, "layered_wall.enclosure_temperature_C")
    enclosure_status = raw.get("enclosure_temperature_status")
    if enclosure_status is not None and (
        not isinstance(enclosure_status, str) or not enclosure_status.strip()
    ):
        raise ValueError(
            "layered_wall enclosure_temperature_status must be a string"
        )
    return {
        "source_tag": source_tag,
        "outer_temperature_C": outer,
        "outer_temperature_status": outer_status,
        "enclosure_temperature_C": enclosure,
        "enclosure_temperature_status": enclosure_status,
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
        "source_status": layer.get("source_status"),
        "thickness_m": layer.get("thickness_m"),
        "thickness_status": layer.get("thickness_status"),
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
            "reason": "see_insulation_dew_point",
            "detail": (
                "The stack has a porous felt. Whether vapour condenses in "
                "it is the insulation dew-point record. A missing partial, "
                "a singular equilibrium, or a dew temperature inside the "
                "felt stays flagged. An intact liner is not assumed. Live "
                "SiO, Na, and K rows are equilibrium pressures, not pure "
                "saturation."
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
        gas = liner.get("gas_tight") if isinstance(liner, Mapping) else None
        intact = (
            isinstance(gas, Mapping)
            and gas.get("certified") is True
            and gas.get("gas_tight") is True
        )
        if not intact:
            flags.append({
                "code": "cao_shell_not_behind_intact_liner",
                "status": "flagged",
                "detail": (
                    "Liner gas-tightness is not certified, so the CaO shell "
                    "is not claimed to sit behind an intact liner. A set "
                    "thickness does not certify that."
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
    design_point: bool = False,
    hold_temperature_C: float | None = None,
) -> dict[str, Any]:
    """Compare duct loss with what the flue can be shown to carry.

    Conductive loss uses the layered wall. A layer with no conductivity is
    omitted and the last known face radiates to the enclosure. That loss
    is an estimate, not a bound. The carrier side uses the ideal-gas
    density and the two printed JANAF N2 Cp rows that bracket the gas
    temperature. It does not interpolate Cp. σT^4 of a black surface is
    only a ceiling.

    When design_point is set, the stream is the printed t-1884 SiO peak,
    not the overhead pipe capacity. Capacity at 5–15 mbar is reported
    beside it and is not the verdict. The wall walk uses the duct-band
    hot face when that hold temperature is supplied. dT is the campaign
    gas temperature minus that face. The stream can surrender that
    enthalpy once along the duct, so the per-metre figure divides by the
    duct length.
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
    # The face the stream is supposed to hold is the duct band. The gas
    # temperature stays the stream temperature. With no hold temperature
    # the walk uses the gas temperature, which is the only face given.
    if hold_temperature_C is None:
        face_C = temperature_C
    else:
        face_C = _finite_number(hold_temperature_C, "hold_temperature_C")
    catalog = _catalog(materials)
    resolved = [
        resolve_layer_properties(
            layer,
            materials=catalog,
            temperature_K=face_C + CELSIUS_TO_KELVIN_OFFSET,
        )
        for layer in wall["layers"]
    ]
    conduction = _solve_wall_conduction(
        resolved,
        face_C,
        wall,
        radius_m=radius_m,
        emissivity=_YSZ_TBC_EMISSIVITY,
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
    loss_vs_black_body = _loss_vs_black_body(conduction, radius_m, black_body)
    if design_point:
        stream = _design_vapour_stream(
            gas_temperature_C=temperature_C,
            hold_temperature_C=hold_temperature_C,
            carrier_gas=carrier_gas,
            pipe_diameter_m=diameter_m,
            pressure_mbar_low=low_mbar,
            pressure_mbar_high=high_mbar,
            conduction=conduction,
        )
    else:
        stream = {
            "status": "unavailable",
            "reason": "mass_flow_unset",
            "stream_power_W": _unavailable("mass_flow_unset", "W"),
            "trace_heating_power_W_per_m": _unavailable("mass_flow_unset", "W/m"),
            "trace_heating_power_W": _unavailable("mass_flow_unset", "W"),
            "trace_heating_verdict": "trace_heating_power_unavailable",
            "verdict": "hot_face_hold_undetermined",
            "pipe_length_status": "unset_in_setpoints",
        }
    return {
        "campaign": campaign,
        "carrier_gas": carrier_gas,
        "gas_temperature_C": temperature_C,
        "gas_temperature_K": temperature_K,
        "pressure_mbar": [low_mbar, high_mbar],
        "pipe_diameter_m": diameter_m,
        "length_basis": "per_metre_of_duct",
        "ideal_gas_density_kg_m3": densities,
        "n2_cp_bounds": cp_bounds,
        "volumetric_heat_J_per_m3_K": volumetric,
        "inventory_heat_J_per_m_K": _inventory_heat(volumetric, radius_m),
        "stream_power_W": stream["stream_power_W"],
        "design_vapour_stream": stream,
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
        "loss_vs_black_body": loss_vs_black_body,
        "verdict": stream["verdict"],
        "trace_heating_power_W_per_m": stream["trace_heating_power_W_per_m"],
        "trace_heating_power_W": stream["trace_heating_power_W"],
        "trace_heating_verdict": stream.get(
            "trace_heating_verdict", "trace_heating_power_unavailable"
        ),
        "pipe_length_m": stream.get("pipe_length_m"),
        "pipe_length_status": stream.get(
            "pipe_length_status", "unset_in_setpoints"
        ),
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
            duct_radius_m=_sweep_diameter(setpoints) / 2.0,
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
                design_point=True,
                hold_temperature_C=bands[HOT_DUCT_TRAIN_KEY][1],
            )
            for row in _continuum_sweeps(setpoints)
        ],
        "felt_overheat_alternatives": felt_overheat_alternatives(
            parameters.layered_wall,
            bands,
            duct_radius_m=_sweep_diameter(setpoints) / 2.0,
            import_delivery_cost_per_kg=parameters.import_delivery_cost_per_kg,
            materials=catalog,
        ),
        "pressure_bands_without_flue_state": _bare_pressure_bands(setpoints),
        "in_situ_bootstrap_cost_per_kg": parameters.in_situ_bootstrap_cost_per_kg,
        "in_situ_cost_derivation": {
            "doloma": in_situ_processing_cost_per_kg(
                "doloma", parameters.electricity_cost_per_kWh
            ),
            "silica": in_situ_processing_cost_per_kg(
                "silica", parameters.electricity_cost_per_kWh
            ),
        },
    }


def interface_temperature_table(
    layered_wall: Mapping[str, Any],
    bands_C: Mapping[str, tuple[float, float]],
    *,
    materials: Mapping[str, Any] | None = None,
    duct_radius_m: float | None = None,
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
        radius_m=duct_radius_m,
        design_dew=True,
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
    source_status = _optional_status(
        raw.get("source_status"), f"layered_wall.{key}.source_status"
    )
    thickness = raw.get("thickness_m")
    if thickness is not None:
        thickness = _finite_number(thickness, f"layered_wall.{key}.thickness_m")
        if thickness <= 0.0:
            raise ValueError(f"layered_wall.{key}.thickness_m must be positive")
    thickness_status = _optional_status(
        raw.get("thickness_status"), f"layered_wall.{key}.thickness_status"
    )
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
        "source_status": source_status,
        "thickness_m": thickness,
        "thickness_status": thickness_status,
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
    cost = entry.get("cost_per_kg_usd")
    if cost is not None:
        cost = _finite_number(cost, "layered_wall.stage_bulk.sio_stage3.cost_per_kg_usd")
        if cost < 0.0:
            raise ValueError("sio_stage3 cost_per_kg_usd must be non-negative")
    cost_status = _optional_status(
        entry.get("cost_status"), "layered_wall.stage_bulk.sio_stage3.cost_status"
    )
    return {
        _STAGE3_AREA_KEY: {
            "kind": "in_situ_product_bulk",
            "material_id": material_id,
            "source": "in_situ",
            "cost_per_kg_usd": cost,
            "cost_status": cost_status,
        }
    }


def _optional_status(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or null")
    return value


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
    # Its USD/kg is the stage-bulk rate when that entry names one. The
    # doloma scalar is the fallback only when the stage entry has no
    # cost status, so the two oxides are not blended into one rate.
    mass_kg = area_m2 * thickness_m * float(density_kg_m3)
    stage_rate, stage_rate_reason = _product_bulk_rate(
        bulk, in_situ_bootstrap_cost_per_kg
    )
    in_situ_usd = _price_known_mass(
        mass_kg,
        stage_rate,
        stage_rate_reason,
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


def _product_bulk_rate(
    bulk: Mapping[str, Any],
    fallback: float | None,
) -> tuple[float | None, str]:
    """Stage-3 USD/kg. A number on the entry wins. Derived-but-unresolved refuses.

    A null cost with no status keeps the doloma scalar so the test fixture
    and an older wall still price. owner_unset null does not use that scalar.
    """

    cost = bulk.get("cost_per_kg_usd")
    status = bulk.get("cost_status")
    if cost is not None:
        return float(cost), "in_situ_cost_unset"
    if status == _DERIVED_COST_STATUS:
        return None, "derived_cost_unresolved"
    if status == _OWNER_UNSET_STATUS:
        return None, "in_situ_cost_unset"
    return fallback, "in_situ_cost_unset"


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
    """An assumed layer override replaces the catalog. A cited disagreement raises.

    The test wall carries assumed densities that are not the catalog
    measurements. Those scalars are the test input. A cited override that
    names a different number than the catalog is a conflict, not a silent pick.
    """

    role = str(layer["role"])
    override = layer.get("density_kg_m3")
    status = layer.get("density_status")
    catalog_value = entry.get("density_kg_m3") if isinstance(entry, Mapping) else None
    catalog_flag = None
    if isinstance(entry, Mapping):
        catalog_flag = entry.get("density_flag") or entry.get("density_status")
    if override is not None:
        if (
            status == "cited"
            and catalog_value is not None
            and float(catalog_value) != float(override)
        ):
            raise ValueError(f"density_conflict:{role}")
        return float(override), None, str(status or "override")
    if catalog_value is not None:
        return float(catalog_value), None, str(catalog_flag or "catalog")
    return None, f"density_missing:{role}", None


def _resolve_conductivity(
    layer: Mapping[str, Any],
    entry: Mapping[str, Any] | None,
    *,
    temperature_K: float | None,
) -> tuple[float | None, str | None, str | None]:
    """An assumed scalar or curve replaces the catalog curve.

    Mass pricing calls this without a temperature. A catalog curve then
    returns thermal_conductivity_temperature_unset and does not wipe the
    density. A cited scalar that disagrees with a catalog scalar or curve
    raises. The shipped layers do not carry a conductivity override, so
    they use the catalog.
    """

    role = str(layer["role"])
    override_scalar = layer.get("conductivity_W_m_K")
    override_curve = layer.get("thermal_conductivity")
    status = layer.get("conductivity_status")
    catalog_scalar = None
    catalog_curve = None
    extension = None
    if isinstance(entry, Mapping):
        catalog_scalar = entry.get("conductivity_W_m_K")
        if catalog_scalar is None:
            catalog_scalar = entry.get("thermal_conductivity_W_m_K")
        catalog_curve = entry.get("thermal_conductivity")
        extension = entry.get("conductivity_extension")
    has_override = override_scalar is not None or override_curve is not None
    if has_override and status == "assumed":
        if override_curve is not None:
            return _select_curve(override_curve, temperature_K, role, extension=None)
        return float(override_scalar), None, "assumed"
    if has_override and status == "cited":
        if override_scalar is not None and catalog_scalar is not None:
            if float(override_scalar) != float(catalog_scalar):
                raise ValueError(f"conductivity_conflict:{role}")
        if override_scalar is not None and _is_curve(catalog_curve):
            raise ValueError(f"conductivity_conflict:{role}")
        if override_curve is not None and (
            catalog_scalar is not None or _is_curve(catalog_curve)
        ):
            raise ValueError(f"conductivity_conflict:{role}")
        if override_curve is not None:
            return _select_curve(
                override_curve, temperature_K, role, extension=extension
            )
        return float(override_scalar), None, "cited"
    if has_override:
        if override_curve is not None:
            return _select_curve(override_curve, temperature_K, role, extension=None)
        return float(override_scalar), None, "k_not_a_curve"
    if _is_curve(catalog_curve):
        return _select_curve(
            catalog_curve, temperature_K, role, extension=extension
        )
    if catalog_scalar is not None:
        return float(catalog_scalar), None, "k_not_a_curve"
    return None, f"thermal_conductivity_missing:{role}", None


def _is_curve(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and bool(value)


def _select_curve(
    curve: Sequence[Any],
    temperature_K: float | None,
    role: str,
    *,
    extension: str | None,
) -> tuple[float | None, str | None, str | None]:
    """Piecewise-linear inside the printed grid. Nearest point only when asked.

    Off-grid nearest-point use is the catalog's conductivity_extension, not
    a silent extrapolation and not a fabricated grid point. Without that
    flag, a temperature outside the printed span is not_on_cited_grid.
    """

    if temperature_K is None:
        return None, f"thermal_conductivity_temperature_unset:{role}", None
    points = sorted(
        (float(point["temperature_K"]), float(point["value_W_m_K"]))
        for point in curve
    )
    target = float(temperature_K)
    for printed_t, value in points:
        if abs(printed_t - target) <= _TEMPERATURE_MATCH_K:
            return value, None, "cited_grid"
    below = None
    above = None
    for printed_t, value in points:
        if printed_t < target and (below is None or printed_t > below[0]):
            below = (printed_t, value)
        elif printed_t > target and (above is None or printed_t < above[0]):
            above = (printed_t, value)
    if below is not None and above is not None:
        span = above[0] - below[0]
        fraction = (target - below[0]) / span
        interpolated = below[1] + fraction * (above[1] - below[1])
        return interpolated, None, "k_interpolated_not_on_printed_grid"
    if extension == "nearest_cited_point":
        nearest = min(points, key=lambda point: abs(point[0] - target))
        return nearest[1], None, "k_extended_off_cited_temperature"
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
        if product_surface:
            detail = (
                "The wall catalog has no gas_tight or permeability_m2 value "
                "for the gas-facing surface."
            )
        else:
            detail = (
                "The wall catalog has no gas_tight or permeability_m2 value "
                "for the gas-facing liner. Bulk density does not certify "
                "gas tightness. The measurement that would clear this flag "
                "is the open porosity (ASTM C20) or the helium permeability "
                "of the dense 10YSZ tile compared with plasma-sprayed YSZ."
            )
        return [{
            "code": code,
            "status": "flagged",
            "detail": detail,
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
    radius_m: float | None = None,
    design_dew: bool = False,
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
            "insulation_dew_point": {
                "status": "not_applicable_no_insulation_layer",
                "reason": "product_baffle_has_no_insulation_layer",
            },
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
    conduction = _solve_wall_conduction(
        evaluated,
        hot_face,
        wall,
        radius_m=radius_m,
        emissivity=_YSZ_TBC_EMISSIVITY,
    )
    walk = conduction["status"] in _CONDUCTION_WALK_STATUSES
    temperatures = _temperatures_by_role(conduction) if walk else {}
    layer_rows = []
    for index, layer in enumerate(evaluated):
        service = layer["service"]
        pair = temperatures.get(layer["role"])
        if pair is not None:
            inner, outer = pair
            temperature_flags = _service_flags(inner, service)
        else:
            reason = (
                "structure_omitted_from_temperature_walk"
                if walk
                else _reason_of(conduction)
            )
            inner = _unavailable(reason, "C")
            outer = _unavailable(reason, "C")
            temperature_flags = (
                _service_flags(hot_face, service)
                if index == 0
                else ["layer_temperature_unchecked"]
            )
        layer_rows.append({
            "role": layer["role"],
            "material_id": layer.get("material_id"),
            "source": layer.get("source"),
            "source_status": layer.get("source_status"),
            "thickness_m": layer.get("thickness_m"),
            "thickness_status": layer.get("thickness_status"),
            "density_kg_m3": layer.get("density_kg_m3"),
            "density_reason": layer.get("density_reason"),
            "density_flag": layer.get("density_flag"),
            "conductivity_W_m_K": layer.get("conductivity_W_m_K"),
            "conductivity_reason": layer.get("conductivity_reason"),
            "conductivity_flag": layer.get("conductivity_flag"),
            "conductivity_evaluation": (
                "k_evaluated_at_hot_face" if layer.get("conductivity_W_m_K") is not None else None
            ),
            "service": service,
            "hot_face_flags": temperature_flags,
            "inner_temperature_C": inner,
            "outer_temperature_C": outer,
        })
    structure = next(layer for layer in evaluated if layer["role"] == "structure")
    shell_status = "shell_temperature_unchecked"
    structure_pair = temperatures.get("structure")
    if structure_pair is not None and _is_cao_bearing(structure):
        shell_flags = _service_flags(structure_pair[0], structure["service"])
        if "hot_face_above_max_operating" in shell_flags:
            shell_status = "shell_above_max_operating"
        elif "service_temperature_missing" in shell_flags:
            shell_status = "structure_service_temperature_unchecked"
        else:
            shell_status = "shell_within_cited_service"
    felt_pair = temperatures.get("insulation")
    if felt_pair is None:
        felt_inner = _unavailable("felt_temperature_unchecked", "C")
        felt_outer = felt_inner
    else:
        felt_inner, felt_outer = felt_pair
    felt_radius = _felt_inner_radius_m(evaluated, radius_m)
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
        "conduction_authority": conduction.get("authority"),
        "conduction_reason": (
            None if walk else _reason_of(conduction)
        ),
        "layers_omitted": conduction.get("layers_omitted"),
        "boundary_temperature_C": conduction.get("boundary_temperature_C"),
        "boundary_basis": conduction.get("boundary_basis"),
        "radiative_boundary": conduction.get("radiative_boundary"),
        "conduction_geometry": conduction.get("geometry"),
        "insulation_dew_point": insulation_vapour_dew_point(
            felt_inner,
            felt_outer,
            species_partials=_design_duct_partials() if design_dew else None,
            felt_thickness_m=_felt_thickness_m(evaluated),
            geometry="cylindrical" if felt_radius is not None else "flat",
            inner_radius_m=felt_radius,
        ),
        "failure_flags": stack_failure_flags(evaluated),
    }


def _solve_wall_conduction(
    layers: Sequence[Mapping[str, Any]],
    t_in_C: float,
    wall: Mapping[str, Any],
    *,
    radius_m: float | None = None,
    emissivity: float | None = None,
) -> dict[str, Any]:
    """Full series walk, or a known-layer estimate when a suffix has no k.

    A suffix with no conductivity is omitted. When an emissivity is
    supplied, the last known face radiates to the enclosure instead of
    being pinned at the enclosure temperature. That estimate is not an
    upper bound: an off-grid conductivity can be either high or low, and
    a missing outer shell would add resistance. A non-positive
    conductivity or a gap that is not a suffix still refuses. An owner
    outer temperature is the shell outer, so it is used only when every
    layer has a conductivity.
    """

    prefix = _known_conduction_prefix(layers)
    if prefix["blocked"]:
        return _conduction_unavailable(prefix["blocked"])
    known = prefix["known"]
    omitted = prefix["omitted"]
    if not known:
        reasons = [item["reason"] for item in omitted] or ["thermal_conductivity_missing"]
        return _conduction_unavailable(reasons)
    partial = bool(omitted)
    radiate = emissivity is not None and (
        partial or wall.get("outer_temperature_C") is None
    )
    if radiate:
        enclosure = wall.get("enclosure_temperature_C")
        if enclosure is None:
            return _conduction_unavailable(["outer_temperature_unset"])
        try:
            emissivity_value = _finite_number(emissivity, "emissivity")
        except (TypeError, ValueError):
            return _conduction_unavailable(["emissivity_invalid"])
        if not 0.0 < emissivity_value <= 1.0:
            return _conduction_unavailable(["emissivity_invalid"])
        result = _radiative_conduction(
            known,
            t_in_C,
            float(enclosure),
            emissivity_value,
            radius_m,
        )
        basis = "radiative_equilibrium"
    else:
        boundary, basis = _conduction_boundary(wall, partial=partial)
        if boundary is None:
            return _conduction_unavailable(["outer_temperature_unset"])
        if radius_m is None:
            result = flat_wall_conduction(known, t_in_C, boundary)
        else:
            result = cylindrical_wall_conduction(known, radius_m, t_in_C, boundary)
        if result["status"] == "ok":
            result["boundary_temperature_C"] = boundary
    if result["status"] != "ok":
        return result
    result["boundary_basis"] = basis
    result["known_roles"] = [str(layer["role"]) for layer in known]
    result["k_evaluation"] = "k_evaluated_at_hot_face"
    if partial:
        result["status"] = "estimated_known_layers_only"
        result["layers_omitted"] = omitted
        result["authority"] = "estimated_known_layers_only"
        result["not_an_upper_bound"] = True
    else:
        result["authority"] = "all_layers"
        result["layers_omitted"] = []
        result["not_an_upper_bound"] = False
    return result


def _radiative_conduction(
    layers: Sequence[Mapping[str, Any]],
    t_in_C: float,
    t_env_C: float,
    emissivity: float,
    radius_m: float | None,
) -> dict[str, Any]:
    """Gray-body outer face in radiative equilibrium with the enclosure.

    Premise: steady state, no generation, and the heat conducted through
    the known layers leaves the last known face by radiation to a large
    enclosure. The face is gray. The cited emissivity is a YSZ coating
    stand-in, not a doloma shell.

    Algebra: q_outer = ε σ (T_outer^4 - T_env^4). On a flat wall q_outer
    is the series flux. On a cylinder q_outer = Q' / (2 π r_outer). One
    root lies between the enclosure and the hot face because conduction
    falls as the outer face warms and radiation rises.

    Units: σ in W/(m²·K⁴), temperatures in K, flux in W/m². A Celsius
    difference equals the same difference in kelvin.

    Sanity: emissivity of zero is adiabatic and is refused. The flux is
    zero when the outer face is at the enclosure temperature. The root
    is re-checked against ε σ (T^4 - T_env^4) before it is returned.
    """

    t_in = _finite_number(t_in_C, "t_in_C")
    t_env = _finite_number(t_env_C, "enclosure_temperature_C")
    if t_in <= t_env:
        result = _conduct_known(layers, t_in, t_in, radius_m)
        if result["status"] != "ok":
            return result
        solved = t_in
        flux = 0.0
    else:
        lo = t_env
        hi = t_in
        result = _conduct_known(layers, t_in, t_env, radius_m)
        for _ in range(80):
            mid = (lo + hi) / 2.0
            result = _conduct_known(layers, t_in, mid, radius_m)
            if result["status"] != "ok":
                return result
            flux = _outer_surface_flux(result)
            radiant = _gray_body_flux(emissivity, mid, t_env)
            if flux > radiant:
                lo = mid
            else:
                hi = mid
        solved = (lo + hi) / 2.0
        result = _conduct_known(layers, t_in, solved, radius_m)
        if result["status"] != "ok":
            return result
        flux = _outer_surface_flux(result)
        radiant = _gray_body_flux(emissivity, solved, t_env)
        scale = max(abs(flux), abs(radiant), 1.0)
        if abs(flux - radiant) > 1e-6 * scale:
            raise RuntimeError("radiative equilibrium residual")
    result["boundary_temperature_C"] = solved
    result["radiative_boundary"] = {
        "emissivity": emissivity,
        "emissivity_status": "cited_stand_in",
        "emissivity_flag": "emissivity_stand_in_not_doloma_shell",
        "citation": _YSZ_TBC_EMISSIVITY_CITATION,
        "environment_temperature_C": t_env,
        "environment_basis": "enclosure_temperature",
        "radiating_role": str(layers[-1].get("role")),
        "structure_k_status": "doloma_k_uncited",
        "outer_flux_W_m2": flux,
        "outer_flux_law": "epsilon_sigma_T4",
    }
    return result


def _conduct_known(
    layers: Sequence[Mapping[str, Any]],
    t_in_C: float,
    t_out_C: float,
    radius_m: float | None,
) -> dict[str, Any]:
    if radius_m is None:
        return flat_wall_conduction(layers, t_in_C, t_out_C)
    return cylindrical_wall_conduction(layers, radius_m, t_in_C, t_out_C)


def _outer_surface_flux(conduction: Mapping[str, Any]) -> float:
    if conduction.get("geometry") == "cylindrical":
        return float(conduction["heat_flow_W_per_m"]) / (
            2.0 * math.pi * float(conduction["outer_radius_m"])
        )
    return float(conduction["heat_flux_W_m2"])


def _gray_body_flux(emissivity: float, t_surface_C: float, t_env_C: float) -> float:
    surface_K = t_surface_C + CELSIUS_TO_KELVIN_OFFSET
    env_K = t_env_C + CELSIUS_TO_KELVIN_OFFSET
    return emissivity * STEFAN_BOLTZMANN * (surface_K ** 4 - env_K ** 4)


def _known_conduction_prefix(
    layers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    known: list[Mapping[str, Any]] = []
    omitted: list[dict[str, str]] = []
    blocked: list[str] = []
    gap = False
    for layer in layers:
        role = str(layer.get("role") or "layer")
        thickness = layer.get("thickness_m")
        conductivity = layer.get("conductivity_W_m_K")
        if thickness is not None and float(thickness) <= 0.0:
            blocked.append(f"thickness_invalid:{role}")
            continue
        if conductivity is not None and float(conductivity) <= 0.0:
            blocked.append(f"thermal_conductivity_invalid:{role}")
            continue
        ready = thickness is not None and conductivity is not None
        if ready and not gap and not blocked:
            known.append(layer)
            continue
        if ready:
            blocked.append(f"conductivity_gap:{role}")
            continue
        gap = True
        if thickness is None:
            reason = f"thickness_unset:{role}"
        else:
            reason = str(
                layer.get("conductivity_reason")
                or f"thermal_conductivity_missing:{role}"
            )
        omitted.append({"role": role, "reason": reason})
    if blocked:
        return {"known": [], "omitted": omitted, "blocked": blocked}
    return {"known": known, "omitted": omitted, "blocked": []}


def _conduction_boundary(
    wall: Mapping[str, Any],
    *,
    partial: bool,
) -> tuple[float | None, str]:
    """Owner outer temperature is the shell outer, not the felt outer.

    A partial walk uses the enclosure temperature. Using the owner shell
    outer as the felt outer would skip the unknown shell resistance and
    pretend that temperature sat on the felt.
    """

    if not partial and wall.get("outer_temperature_C") is not None:
        return float(wall["outer_temperature_C"]), "owner_outer_temperature"
    enclosure = wall.get("enclosure_temperature_C")
    if enclosure is not None:
        status = wall.get("enclosure_temperature_status") or "enclosure_temperature"
        return float(enclosure), str(status)
    return None, "outer_temperature_unset"


def _temperatures_by_role(conduction: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    interfaces = conduction.get("interface_temperatures_C")
    roles = conduction.get("known_roles") or []
    if not isinstance(interfaces, Sequence) or len(interfaces) != len(roles) + 1:
        return {}
    return {
        str(role): (
            float(interfaces[index]["temperature_C"]),
            float(interfaces[index + 1]["temperature_C"]),
        )
        for index, role in enumerate(roles)
    }


def _loss_vs_black_body(
    conduction: Mapping[str, Any],
    radius_m: float,
    black_body_W_m2: float,
) -> dict[str, Any]:
    """Inner-surface flux against the black-body ceiling.

    Premise: radial heat flow per metre is constant, so the flux the gas
    path must supply is Q' / (2 π r_in). Algebra is that quotient.
    Units: (W/m) / m = W/m². Sanity: a thin wall's inner flux approaches
    the flat-wall flux. A known-layer estimate can sit either side of the
    true loss, so a comparison with σT^4 is not a certified deficit and
    is not a trace-heating wattage.
    """

    if conduction.get("status") not in _CONDUCTION_WALK_STATUSES:
        return {
            "status": "unavailable",
            "reason": conduction.get("reason") or "conductive_loss_unavailable",
        }
    heat_flow = conduction.get("heat_flow_W_per_m")
    if isinstance(heat_flow, (int, float)) and not isinstance(heat_flow, bool):
        inner_flux = float(heat_flow) / (2.0 * math.pi * radius_m)
        flux_basis = "cylindrical_inner_surface"
    else:
        heat_flux = conduction.get("heat_flux_W_m2")
        if not isinstance(heat_flux, (int, float)) or isinstance(heat_flux, bool):
            return {"status": "unavailable", "reason": "conductive_loss_unavailable"}
        inner_flux = float(heat_flux)
        flux_basis = "flat_flux"
    relation = (
        "estimated_loss_below_black_body_ceiling"
        if inner_flux < black_body_W_m2
        else "estimated_loss_above_black_body_ceiling"
    )
    return {
        "status": conduction["status"],
        "authority": conduction.get("authority"),
        "inner_flux_W_m2": inner_flux,
        "flux_basis": flux_basis,
        "black_body_flux_W_m2": black_body_W_m2,
        "relation": relation,
        "not_an_upper_bound": bool(conduction.get("not_an_upper_bound")),
        "not_a_certified_deficit": True,
        "detail": (
            "Off-grid conductivity can err either way. This comparison is "
            "not an upper bound and not a trace-heating wattage."
        ),
    }


def insulation_vapour_dew_point(
    felt_inner_temperature_C: Any,
    felt_outer_temperature_C: Any,
    *,
    species_partials: Mapping[str, Any] | None = None,
    felt_thickness_m: float | None = None,
    geometry: str = "flat",
    inner_radius_m: float | None = None,
) -> dict[str, Any]:
    """Felt dew point from equilibrium partials, or a typed refusal.

    Live SiO, Na, and K rows are equilibrium pressures of oxide reactions,
    not pure-component saturation. With no partials, nothing is inverted.
    A supplied partial is inverted with the vapour-rail evaluator. pO2 of
    zero makes an oxygen-dependent equilibrium singular. Depth uses the
    walked felt profile: linear in thickness on a flat wall, linear in
    ln(r) on a cylinder.
    """

    species = {
        "SiO": _dew_species_shell("oxide_vapors_sio_family"),
        "Na": _dew_species_shell("metals_na_family"),
        "K": _dew_species_shell("metals_k_family"),
    }
    if species_partials is None:
        return {
            "status": "unavailable",
            "reason": "partial_pressure_unset",
            "felt_inner_temperature_C": felt_inner_temperature_C,
            "felt_outer_temperature_C": felt_outer_temperature_C,
            "species": species,
        }
    for name in species:
        supplied = species_partials.get(name)
        species[name].update(
            _invert_design_partial(
                name,
                supplied if isinstance(supplied, Mapping) else None,
                felt_inner_temperature_C,
                felt_outer_temperature_C,
                felt_thickness_m=felt_thickness_m,
                geometry=geometry,
                inner_radius_m=inner_radius_m,
            )
        )
    return {
        "status": "design_point",
        "reason": "design_point_equilibrium_partial",
        "pressure_kind": "equilibrium_partial_pressure",
        "felt_inner_temperature_C": felt_inner_temperature_C,
        "felt_outer_temperature_C": felt_outer_temperature_C,
        "species": species,
    }


def equilibrium_pressure_pa(
    species_id: str,
    temperature_K: float,
    *,
    pO2_bar: float,
    source_activity: float,
) -> float:
    """Equilibrium partial from the vapour-rail catalog, in pascals."""

    evaluator = _vapour_catalog().evaluator_for(species_id)
    return float(
        evaluator.evaluate(
            temperature_K,
            source_activity=source_activity,
            pO2_bar=pO2_bar,
        ).pressure_pa
    )


def felt_overheat_alternatives(
    layered_wall: Mapping[str, Any],
    bands_C: Mapping[str, tuple[float, float]],
    *,
    duct_radius_m: float | None,
    import_delivery_cost_per_kg: float,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """YSZ thickness that holds the felt at its continuous limit.

    The shipped 5 mm liner is not changed. An intermediate dense layer is
    not priced: no crop-verified conductivity and density for a
    high-alumina castable or fireclay is stored. The USD/m² quote is the
    flat areal identity thickness × density × import price. The duct
    temperature walk is cylindrical.
    """

    wall = normalize_layered_wall(layered_wall)
    catalog = _catalog(materials)
    rate = _finite_number(
        import_delivery_cost_per_kg, "import_delivery_cost_per_kg"
    )
    rows: dict[str, Any] = {}
    rows["hot_duct"] = _felt_limit_row(
        wall,
        float(bands_C[HOT_DUCT_TRAIN_KEY][1]),
        radius_m=duct_radius_m,
        import_rate=rate,
        materials=catalog,
    )
    for area_key, _number, train_key in COLLECTOR_STAGE_JOIN:
        if area_key == _STAGE3_AREA_KEY:
            rows[area_key] = {
                "status": "not_applicable_no_insulation_layer",
                "reason": "product_baffle_has_no_insulation_layer",
            }
            continue
        rows[area_key] = _felt_limit_row(
            wall,
            float(bands_C[train_key][1]),
            radius_m=None,
            import_rate=rate,
            materials=catalog,
        )
    rows["intermediate_dense_layer"] = {
        "status": "unavailable",
        "reason": "intermediate_layer_uncited",
        "usd_per_m2": None,
        "detail": (
            "No crop-verified thermal conductivity and density for a "
            "high-alumina castable or fireclay was stored. A rendered "
            "crop of that table would clear the price. The layer is not "
            "inserted into the shipped wall."
        ),
    }
    rows["comparison"] = {
        "status": "comparison_incomplete",
        "reason": "intermediate_layer_uncited",
        "priced_alternative": "thicker_ysz_liner",
        "shipped_liner_unchanged": True,
        "detail": (
            "The shipped liner thickness is unchanged and is flagged "
            "where the felt exceeds its continuous-service limit. The "
            "thicker YSZ liner is the only priced alternative that "
            "meets that limit. It costs more per square metre than the "
            "shipped liner. The intermediate layer was not priced."
        ),
    }
    return rows


def _dew_species_shell(family: str) -> dict[str, Any]:
    return {
        "family": family,
        "pressure_kind": "equilibrium_partial_pressure",
        "reason": "equilibrium_not_pure_saturation",
    }


def _design_duct_partials() -> dict[str, Any]:
    return {
        "SiO": {
            "printed": True,
            "partial_pressure_Pa": _DESIGN_DUCT_SIO_PARTIAL_PA,
            "pO2_mbar": _DESIGN_DUCT_PO2_MBAR,
            "hour": _DESIGN_POINT_HOUR,
            "location": _DESIGN_POINT_LOCATION,
            "label": _DESIGN_POINT_LABEL,
            "source_activity": None,
        },
        "Na": {"printed": False},
        "K": {"printed": False},
    }


def _felt_inner_radius_m(
    layers: Sequence[Mapping[str, Any]],
    gas_radius_m: float | None,
) -> float | None:
    if gas_radius_m is None:
        return None
    cursor = gas_radius_m
    for layer in layers:
        if layer.get("role") == "insulation":
            return cursor
        thickness = layer.get("thickness_m")
        if not isinstance(thickness, (int, float)) or isinstance(thickness, bool):
            return None
        cursor += float(thickness)
    return None


def _felt_thickness_m(layers: Sequence[Mapping[str, Any]]) -> float | None:
    for layer in layers:
        if layer.get("role") == "insulation":
            thickness = layer.get("thickness_m")
            if isinstance(thickness, (int, float)) and not isinstance(thickness, bool):
                return float(thickness)
    return None


def _invert_design_partial(
    species_id: str,
    supplied: Mapping[str, Any] | None,
    felt_inner_C: Any,
    felt_outer_C: Any,
    *,
    felt_thickness_m: float | None,
    geometry: str,
    inner_radius_m: float | None,
) -> dict[str, Any]:
    if supplied is None or not supplied.get("printed"):
        return {
            "status": "unavailable",
            "reason": "duct_inlet_partial_not_in_design_print",
            "dew_temperature_C": None,
            "depth_m": None,
            "clearing_print": (
                f"duct-inlet partial pressure of {species_id} at "
                f"{_DESIGN_POINT_LABEL} hour {_DESIGN_POINT_HOUR}"
            ),
        }
    partial = supplied.get("partial_pressure_Pa")
    pO2_mbar = supplied.get("pO2_mbar")
    if not isinstance(partial, (int, float)) or isinstance(partial, bool):
        return {"status": "unavailable", "reason": "partial_pressure_unset"}
    if not isinstance(pO2_mbar, (int, float)) or isinstance(pO2_mbar, bool):
        return {"status": "unavailable", "reason": "pO2_unset"}
    if float(partial) <= 0.0:
        return {"status": "unavailable", "reason": "partial_pressure_invalid"}
    evaluator = _vapour_catalog().evaluator_for(species_id)
    oxygen_dependent = abs(float(evaluator.pO2_exponent)) > 0.0 or (
        evaluator.o2_channel_term is not None
    )
    if oxygen_dependent and float(pO2_mbar) <= 0.0:
        return {
            "status": "unavailable",
            "reason": "equilibrium_singular_at_zero_pO2",
            "partial_pressure_Pa": float(partial),
            "pO2_mbar": float(pO2_mbar),
            "pO2_exponent": float(evaluator.pO2_exponent),
            "hour": supplied.get("hour"),
            "location": supplied.get("location"),
            "label": supplied.get("label"),
            "flag": "design_point",
            "dew_temperature_C": None,
            "depth_m": None,
            "clearing_print": (
                "a non-zero pO2_mbar on the same duct-inlet row as the "
                f"{species_id} partial"
            ),
        }
    activity = supplied.get("source_activity")
    activity_flag = None
    if activity is None:
        activity = 1.0
        activity_flag = "raoultian_pure_endmember_not_a_measured_melt_activity"
    found = _invert_equilibrium_temperature(
        evaluator,
        float(partial),
        pO2_bar=float(pO2_mbar) / MBAR_PER_BAR,
        source_activity=float(activity),
    )
    if found["status"] != "ok":
        return found
    depth = _dew_depth_m(
        found["dew_temperature_C"],
        felt_inner_C,
        felt_outer_C,
        felt_thickness_m=felt_thickness_m,
        geometry=geometry,
        inner_radius_m=inner_radius_m,
    )
    found["depth_m"] = depth["depth_m"]
    found["depth_status"] = depth["status"]
    found["depth_reason"] = depth.get("reason")
    found["activity_flag"] = activity_flag
    found["source_activity"] = float(activity)
    found["flag"] = "design_point"
    found["reason"] = "equilibrium_not_pure_saturation"
    found["pressure_kind"] = "equilibrium_partial_pressure"
    return found


def _invert_equilibrium_temperature(
    evaluator: Any,
    partial_pressure_Pa: float,
    *,
    pO2_bar: float,
    source_activity: float,
) -> dict[str, Any]:
    low, high = evaluator.valid_temperature_K

    def pressure_at(temperature_K: float) -> float:
        return float(
            evaluator.evaluate(
                temperature_K,
                source_activity=source_activity,
                pO2_bar=pO2_bar,
            ).pressure_pa
        )

    p_low = pressure_at(float(low))
    p_high = pressure_at(float(high))
    if p_high == p_low:
        return {
            "status": "unavailable",
            "reason": "equilibrium_pressure_not_monotone",
        }
    increasing = p_high > p_low
    if increasing and not p_low <= partial_pressure_Pa <= p_high:
        return _dew_outside_domain(p_low, p_high, low, high)
    if not increasing and not p_high <= partial_pressure_Pa <= p_low:
        return _dew_outside_domain(p_low, p_high, low, high)
    lo = float(low)
    hi = float(high)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        pressure = pressure_at(mid)
        if (pressure < partial_pressure_Pa) == increasing:
            lo = mid
        else:
            hi = mid
    dew_K = (lo + hi) / 2.0
    return {
        "status": "ok",
        "reason": None,
        "dew_temperature_K": dew_K,
        "dew_temperature_C": dew_K - CELSIUS_TO_KELVIN_OFFSET,
        "partial_pressure_Pa": partial_pressure_Pa,
        "pO2_bar": pO2_bar,
        "valid_temperature_K": [float(low), float(high)],
    }


def _dew_outside_domain(
    p_low: float,
    p_high: float,
    low: float,
    high: float,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": "partial_outside_catalog_domain",
        "domain_pressure_Pa": [p_low, p_high],
        "valid_temperature_K": [float(low), float(high)],
        "dew_temperature_C": None,
        "depth_m": None,
    }


def _dew_depth_m(
    dew_C: float,
    felt_inner_C: Any,
    felt_outer_C: Any,
    *,
    felt_thickness_m: float | None,
    geometry: str,
    inner_radius_m: float | None,
) -> dict[str, Any]:
    """Where the dew temperature sits in the felt.

    Premise: conductivity is uniform through the felt, so temperature is
    linear in depth on a flat wall and linear in ln(r) on a cylinder.
    Algebra, flat: depth = t (T_inner - T_dew) / (T_inner - T_outer).
    Cylinder: the same fraction is ln(r / r_in) / ln(r_out / r_in).
    Units: temperatures in °C, depth in m. Sanity: the dew face is at
    the inner face when T_dew = T_inner and at the outer face when
    T_dew = T_outer.
    """

    if (
        not isinstance(felt_inner_C, (int, float))
        or not isinstance(felt_outer_C, (int, float))
        or isinstance(felt_inner_C, bool)
        or isinstance(felt_outer_C, bool)
        or felt_thickness_m is None
    ):
        return {"status": "unavailable", "reason": "felt_profile_unset", "depth_m": None}
    span = float(felt_inner_C) - float(felt_outer_C)
    if span == 0.0:
        return {"status": "unavailable", "reason": "felt_isothermal", "depth_m": None}
    fraction = (float(felt_inner_C) - dew_C) / span
    if fraction < 0.0:
        return {
            "status": "hotter_than_felt_inner_face",
            "reason": "dew_before_the_felt",
            "depth_m": None,
        }
    if fraction > 1.0:
        return {
            "status": "colder_than_felt_outer_face",
            "reason": "dew_outside_the_felt",
            "depth_m": None,
        }
    if geometry == "cylindrical":
        if inner_radius_m is None or inner_radius_m <= 0.0:
            return {"status": "unavailable", "reason": "inner_radius_unset", "depth_m": None}
        outer = inner_radius_m + felt_thickness_m
        radius = inner_radius_m * (outer / inner_radius_m) ** fraction
        depth = radius - inner_radius_m
    else:
        depth = felt_thickness_m * fraction
    return {"status": "inside_felt", "depth_m": depth}


def _vapour_catalog() -> Any:
    global _VAPOUR_CATALOG
    if _VAPOUR_CATALOG is None:
        from simulator.corpus_version import repo_root
        from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
        from simulator.yaml_cache import load_cached_safe_yaml

        payload = load_cached_safe_yaml(
            (repo_root() / "data" / "vapor_pressures.yaml").read_text(encoding="utf-8")
        )
        _VAPOUR_CATALOG = compile_vapour_rail_catalog(
            payload,
            emit_u0_request_rules=False,
        )
    return _VAPOUR_CATALOG


def _felt_limit_row(
    wall: Mapping[str, Any],
    hot_face_C: float,
    *,
    radius_m: float | None,
    import_rate: float,
    materials: Mapping[str, Any],
) -> dict[str, Any]:
    evaluated = [
        resolve_layer_properties(
            layer,
            materials=materials,
            temperature_K=hot_face_C + CELSIUS_TO_KELVIN_OFFSET,
        )
        for layer in wall["layers"]
    ]
    shipped = _solve_wall_conduction(
        evaluated,
        hot_face_C,
        wall,
        radius_m=radius_m,
        emissivity=_YSZ_TBC_EMISSIVITY,
    )
    felt = next(layer for layer in evaluated if layer["role"] == "insulation")
    liner = next(layer for layer in evaluated if layer["role"] == "liner")
    continuous = felt["service"].get("continuous_C")
    shipped_inner = _felt_inner_C(shipped)
    shipped_price = _liner_areal_price(liner, import_rate)
    base = {
        "hot_face_C": hot_face_C,
        "geometry": "cylindrical" if radius_m is not None else "flat",
        "shipped_liner_thickness_m": liner.get("thickness_m"),
        "shipped_felt_inner_C": shipped_inner,
        "felt_continuous_service_C": continuous,
        "shipped_liner_usd_per_m2": shipped_price,
        "usd_per_m2_basis": "flat_areal_thickness_times_density_times_import_price",
        "intermediate_layer_reason": "intermediate_layer_uncited",
        "radiative_boundary": shipped.get("radiative_boundary"),
    }
    if continuous is None:
        base.update(status="unavailable", reason="felt_continuous_service_unset")
        return base
    if shipped["status"] not in _CONDUCTION_WALK_STATUSES or shipped_inner is None:
        base.update(
            status="unavailable",
            reason=_reason_of(shipped) if shipped["status"] == "unavailable" else "felt_temperature_unchecked",
        )
        return base
    if shipped_inner <= float(continuous):
        base.update(
            status="within_continuous_service",
            felt_overheats_at_shipped_thickness=False,
        )
        return base
    solved = _minimum_liner_thickness(
        evaluated,
        hot_face_C,
        wall,
        radius_m=radius_m,
        limit_C=float(continuous),
        shipped_thickness_m=float(liner["thickness_m"]),
    )
    base["felt_overheats_at_shipped_thickness"] = True
    base["status"] = solved["status"]
    base["reason"] = solved.get("reason")
    if solved["status"] != "ok":
        return base
    priced = dict(liner)
    priced["thickness_m"] = solved["thickness_m"]
    base.update(
        minimum_liner_thickness_m=solved["thickness_m"],
        felt_inner_C_at_solved_thickness=solved["felt_inner_C"],
        thicker_liner_usd_per_m2=_liner_areal_price(priced, import_rate),
        cheaper_per_m2="shipped_liner_is_cheaper_and_overheats",
    )
    if radius_m is not None:
        base["thicker_liner_usd_per_m"] = _cylindrical_liner_usd_per_m(
            priced, radius_m, import_rate
        )
        shipped_cyl = dict(liner)
        base["shipped_liner_usd_per_m"] = _cylindrical_liner_usd_per_m(
            shipped_cyl, radius_m, import_rate
        )
    return base


def _felt_inner_C(conduction: Mapping[str, Any]) -> float | None:
    pair = _temperatures_by_role(conduction).get("insulation")
    if pair is None:
        return None
    return pair[0]


def _minimum_liner_thickness(
    layers: Sequence[Mapping[str, Any]],
    hot_face_C: float,
    wall: Mapping[str, Any],
    *,
    radius_m: float | None,
    limit_C: float,
    shipped_thickness_m: float,
) -> dict[str, Any]:
    """Thinnest liner whose felt inner face is at or below the limit.

    Premise: the felt inner face is the hot face minus the drop in the
    liner. A thicker liner takes more of that drop, so the felt cools.
    Resistance added outside the felt does the opposite. The outer
    temperature is the radiative root, so the thickness is the production
    walk's root, not a pinned-wall closed form.

    Algebra of one trial: the walk in _solve_wall_conduction. The search
    keeps a too-thin bracket and a thick-enough bracket.
    Units: thickness in m, temperature in °C.
    Sanity: a liner thin enough that the felt is still over the limit is
    rejected; the returned walk is re-read and must sit on the limit.
    """

    hi = max(shipped_thickness_m, 1e-4)
    probe = _felt_inner_at_thickness(
        layers, hi, hot_face_C, wall, radius_m
    )
    if probe is None:
        return {"status": "unavailable", "reason": "felt_temperature_unchecked"}
    while probe > limit_C:
        hi *= 2.0
        if hi > _FELT_LIMIT_THICKNESS_CAP_M:
            return {
                "status": "unavailable",
                "reason": "liner_thickness_does_not_reach_felt_limit",
                "thickness_cap_m": _FELT_LIMIT_THICKNESS_CAP_M,
            }
        probe = _felt_inner_at_thickness(layers, hi, hot_face_C, wall, radius_m)
        if probe is None:
            return {"status": "unavailable", "reason": "felt_temperature_unchecked"}
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        inner = _felt_inner_at_thickness(
            layers, mid, hot_face_C, wall, radius_m
        )
        if inner is None:
            return {"status": "unavailable", "reason": "felt_temperature_unchecked"}
        if inner > limit_C:
            lo = mid
        else:
            hi = mid
    inner = _felt_inner_at_thickness(layers, hi, hot_face_C, wall, radius_m)
    if inner is None or abs(inner - limit_C) > 0.05:
        return {"status": "unavailable", "reason": "felt_limit_search_residual"}
    return {"status": "ok", "thickness_m": hi, "felt_inner_C": inner}


def _felt_inner_at_thickness(
    layers: Sequence[Mapping[str, Any]],
    thickness_m: float,
    hot_face_C: float,
    wall: Mapping[str, Any],
    radius_m: float | None,
) -> float | None:
    edited = []
    for layer in layers:
        row = dict(layer)
        if row.get("role") == "liner":
            row["thickness_m"] = thickness_m
        edited.append(row)
    walked = _solve_wall_conduction(
        edited,
        hot_face_C,
        wall,
        radius_m=radius_m,
        emissivity=_YSZ_TBC_EMISSIVITY,
    )
    if walked["status"] not in _CONDUCTION_WALK_STATUSES:
        return None
    return _felt_inner_C(walked)


def _liner_areal_price(liner: Mapping[str, Any], import_rate: float) -> Any:
    thickness = liner.get("thickness_m")
    density = liner.get("density_kg_m3")
    if liner.get("source") != "imported":
        return _unavailable("liner_source_not_imported", "USD/m2")
    if thickness is None or density is None:
        return _unavailable("liner_mass_unset", "USD/m2")
    # kg/m² = m × kg/m³. USD/m² = kg/m² × USD/kg.
    return float(thickness) * float(density) * import_rate


def _cylindrical_liner_usd_per_m(
    liner: Mapping[str, Any],
    radius_m: float,
    import_rate: float,
) -> Any:
    rows, _outer = _cylindrical_masses([liner], radius_m, 1.0)
    mass = rows[0]["mass_kg"]
    if _is_unavailable(mass):
        return mass
    return _price_known_mass(float(mass), import_rate, "cost_rate_unset")


def _design_vapour_stream(
    *,
    gas_temperature_C: float,
    hold_temperature_C: float | None,
    carrier_gas: str,
    pipe_diameter_m: float,
    pressure_mbar_low: float,
    pressure_mbar_high: float,
    conduction: Mapping[str, Any],
) -> dict[str, Any]:
    """Sensible heat of the printed SiO peak versus the duct loss.

    Premise: the gas may cool from the campaign gas temperature to the
    duct-band hot face it is supposed to hold. That enthalpy can be given
    up once along the duct, not once per metre. dT is that difference in
    kelvin. Per-metre trace heat divides the once-only stream power by
    the engineering-default duct length and subtracts it from the
    conductive loss per metre.

    Algebra: P = m_dot cp dT, with m_dot in kg/s and cp in J/(kg·K).
    cp is a printed JANAF neighbor of SiO(g), not an interpolation and
    not the N2 table. The overhead Poiseuille capacity is a different
    number and is not m_dot.

    Units: (kg/s) × J/(kg·K) × K = W. Sanity: a zero temperature drop
    leaves no stream power, and a campaign gas colder than the hold
    cannot heat the face.
    """

    from simulator.overhead import (
        ENGINEERING_DEFAULT_PIPE_LENGTH_M,
        compressible_poiseuille_mass_flow,
        gas_dynamic_viscosity_Pa_s,
    )

    length_m = float(ENGINEERING_DEFAULT_PIPE_LENGTH_M)
    length_status = "engineering_default_not_in_setpoints"
    capacity = _overhead_capacity(
        carrier_gas=carrier_gas,
        pipe_diameter_m=pipe_diameter_m,
        length_m=length_m,
        gas_temperature_C=gas_temperature_C,
        pressure_mbar_low=pressure_mbar_low,
        pressure_mbar_high=pressure_mbar_high,
        viscosity=gas_dynamic_viscosity_Pa_s,
        mass_flow=compressible_poiseuille_mass_flow,
    )
    loss = _loss_W_per_m(conduction, pipe_diameter_m / 2.0)
    common = {
        "status": "design_point_single_species_peak",
        "species": "SiO",
        "mass_flow_kg_per_h": _DESIGN_SIO_PEAK_KG_PER_H,
        "mass_flow_flag": "design_point_not_a_simultaneous_sum",
        "label": _DESIGN_POINT_LABEL,
        "hour": _DESIGN_POINT_HOUR,
        "pipe_length_m": length_m,
        "pipe_length_status": length_status,
        "length_certified": False,
        "overhead_pipe_capacity": capacity,
        "dt_premise": (
            "campaign gas temperature minus the duct-band hot face; "
            "the stream surrenders that enthalpy once along the duct"
        ),
    }
    if hold_temperature_C is None:
        refused = _unavailable("hold_temperature_unset", "W")
        common.update(
            status="unavailable",
            reason="hold_temperature_unset",
            stream_power_W=refused,
            trace_heating_power_W_per_m=refused,
            trace_heating_power_W=refused,
            trace_heating_verdict="trace_heating_power_unavailable",
            verdict="hot_face_hold_undetermined",
        )
        return common
    hold = _finite_number(hold_temperature_C, "hold_temperature_C")
    delta = gas_temperature_C - hold
    cp_bounds = _sio_cp_bounds(gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET)
    common["hold_temperature_C"] = hold
    common["gas_temperature_C"] = gas_temperature_C
    common["dT_K"] = delta
    common["cp_bounds"] = cp_bounds
    if _is_unavailable(cp_bounds) or "molar_mass_kg_mol" not in cp_bounds:
        refused = _unavailable(
            _reason_of(cp_bounds) if _is_unavailable(cp_bounds) else "sio_cp_unset",
            "W",
        )
        common.update(
            stream_power_W=refused,
            trace_heating_power_W_per_m=refused,
            trace_heating_power_W=refused,
            trace_heating_verdict="trace_heating_power_unavailable",
            verdict="hot_face_hold_undetermined",
        )
        return common
    powers = _stream_powers_W(cp_bounds, _DESIGN_SIO_PEAK_KG_PER_H, delta)
    common["stream_power_by_neighbor_W"] = powers
    if loss is None:
        refused = _unavailable("conductive_loss_unavailable", "W/m")
        common.update(
            stream_power_W=_unavailable("conductive_loss_unavailable", "W"),
            trace_heating_power_W_per_m=refused,
            trace_heating_power_W=refused,
            trace_heating_verdict="trace_heating_power_unavailable",
            verdict="hot_face_hold_undetermined",
        )
        return common
    # The lower neighbor is the smaller claim that the gas can carry.
    lower = min(powers.values())
    upper = max(powers.values())
    loss_total = loss * length_m
    deficit_lower = loss_total - lower
    deficit_upper = loss_total - upper
    common["conductive_loss_W_per_m"] = loss
    common["conductive_loss_W"] = loss_total
    common["stream_power_W"] = {
        "value": lower,
        "status": "lower_printed_cp_neighbor",
        "units": "W",
    }
    if deficit_lower <= 0.0 and deficit_upper <= 0.0:
        verdict = "hot_face_held_by_design_vapour_stream"
        trace_total = 0.0
    elif deficit_lower > 0.0 and deficit_upper > 0.0:
        verdict = "hot_face_not_held_by_design_vapour_stream"
        trace_total = deficit_lower
    else:
        verdict = "hot_face_hold_bracketed_by_cp_neighbors"
        trace_total = max(deficit_lower, 0.0)
    common["verdict"] = verdict
    common["trace_heating_power_W"] = trace_total
    common["trace_heating_power_W_per_m"] = trace_total / length_m
    common["trace_heating_verdict"] = (
        "trace_heating_required"
        if trace_total > 0.0
        else "design_stream_covers_estimated_loss"
    )
    common["trace_basis"] = (
        "known_layer_radiative_loss_minus_lower_neighbor_stream_power"
    )
    common["not_a_certified_deficit"] = True
    return common


def _stream_powers_W(
    cp_bounds: Mapping[str, Any],
    mass_flow_kg_per_h: float,
    delta_K: float,
) -> dict[str, float]:
    molar_mass = float(cp_bounds["molar_mass_kg_mol"])
    mass_flow = mass_flow_kg_per_h / _SECONDS_PER_HOUR
    points = (
        [("exact", cp_bounds["exact"])]
        if "exact" in cp_bounds
        else [("below", cp_bounds["below"]), ("above", cp_bounds["above"])]
    )
    powers = {}
    for name, point in points:
        if delta_K <= 0.0:
            powers[name] = 0.0
            continue
        cp_per_kg = float(point["cp_J_per_mol_K"]) / molar_mass
        powers[name] = mass_flow * cp_per_kg * delta_K
    return powers


def _loss_W_per_m(conduction: Mapping[str, Any], radius_m: float) -> float | None:
    if conduction.get("status") not in _CONDUCTION_WALK_STATUSES:
        return None
    heat_flow = conduction.get("heat_flow_W_per_m")
    if isinstance(heat_flow, (int, float)) and not isinstance(heat_flow, bool):
        return float(heat_flow)
    heat_flux = conduction.get("heat_flux_W_m2")
    if isinstance(heat_flux, (int, float)) and not isinstance(heat_flux, bool):
        return float(heat_flux) * 2.0 * math.pi * radius_m
    return None


def _overhead_capacity(
    *,
    carrier_gas: str,
    pipe_diameter_m: float,
    length_m: float,
    gas_temperature_C: float,
    pressure_mbar_low: float,
    pressure_mbar_high: float,
    viscosity: Any,
    mass_flow: Any,
) -> dict[str, Any]:
    """Pipe capacity at the sweep pressure against vacuum.

    This is the overhead Poiseuille law, not a metered carrier injection.
    Downstream pressure is zero, which is an upper capacity, and the
    length is the engineering default. The design verdict does not use it.
    """

    if carrier_gas != "N2":
        return _unavailable(f"carrier_cp_table_refused:{carrier_gas}", "kg/s")
    from simulator.transport_constants import CARRIER_GAS_PROPERTIES

    temperature_K = gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET
    molar_mass = CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol
    eta = viscosity(temperature_K)
    rows = {}
    for name, pressure_mbar in (
        ("low_mbar", pressure_mbar_low),
        ("high_mbar", pressure_mbar_high),
    ):
        rows[name] = mass_flow(
            pipe_diameter_m,
            length_m,
            molar_mass,
            eta,
            temperature_K,
            pressure_mbar * PA_PER_MBAR,
            0.0,
        )
    return {
        "status": "overhead_pipe_capacity_against_vacuum",
        "flag": "not_the_design_stream",
        "length_status": "engineering_default_not_in_setpoints",
        "downstream_pressure_Pa": 0.0,
        "kg_per_s": rows,
    }


def _sio_cp_bounds(temperature_K: float) -> dict[str, Any]:
    bounds = _janaf_cp_bounds(
        _SIO_TABLE_ID,
        temperature_K,
        expected_phase="g",
    )
    if _is_unavailable(bounds):
        return bounds
    bounds["molar_mass_kg_mol"] = _molar_mass_g_per_mol("SiO") / 1000.0
    bounds["molar_mass_owner"] = "simulator.accounting.formulas.resolve_species_formula"
    bounds["species"] = "SiO"
    return bounds


def in_situ_processing_cost_per_kg(
    material: str,
    electricity_usd_per_kWh: float,
) -> dict[str, Any]:
    """Sensible-enthalpy processing cost of in-situ doloma or silica.

    Premise: electricity buys the enthalpy to heat the oxide from the
    JANAF 298.15 K reference to the process temperature. No kiln-loss
    measurement is cited, so the thermal efficiency is 1 and the cost is
    a lower bound. Doloma is a stoichiometric CaO+MgO mass mix, not a
    measured doloma heat capacity. Silica uses the quartz crystal table.
    The fused-silica liquid table is referenced to the liquid, and these
    tables do not print a heat of fusion, so none is added.

    Algebra: H is the JANAF enthalpy increment in kJ/mol, linear between
    the printed neighbors. Specific energy in MJ/kg is H / M with M in
    g/mol, because kJ/g = MJ/kg. The doloma mix weights those specific
    energies by molar mass. USD/kg = specific energy / efficiency
    * 1000 / (kJ per kWh) * electricity price. kJ per kWh is
    simulator.thermal_budget.KJ_PER_KWH.

    Units: (MJ/kg) * (1000 kJ/MJ) / (kJ/kWh) * (USD/kWh) = USD/kg.

    Sanity: oxide sensible enthalpy from 25 C to about 1600 C is about
    1-2 MJ/kg. A result outside that band is flagged and still returned.
    """

    electricity = _finite_number(
        electricity_usd_per_kWh, "electricity_cost_per_kWh"
    )
    if electricity < 0.0:
        raise ValueError("electricity_cost_per_kWh must be non-negative")
    if material == "doloma":
        return _doloma_processing_cost(electricity)
    if material == "silica":
        return _silica_processing_cost(electricity)
    raise ValueError("in-situ processing material must be doloma or silica")


def resolve_in_situ_bootstrap_rate(
    value: Any,
    status: Any,
    electricity_usd_per_kWh: float,
) -> Any:
    """Fill the doloma rate only for a derived null. A number, including 0, wins.

    owner_unset null stays null. The caller still rejects a negative number.
    """

    if value is not None:
        return value
    if status == _DERIVED_COST_STATUS:
        return in_situ_processing_cost_per_kg(
            "doloma", electricity_usd_per_kWh
        )["usd_per_kg"]
    return None


def apply_derived_stage_cost(
    wall: Mapping[str, Any],
    electricity_usd_per_kWh: float,
) -> dict[str, Any]:
    """Fill stage-3 silica USD/kg when its status is derived and its value is null.

    An owner number already on the entry is left alone. The doloma scalar
    is not copied onto the silica entry.
    """

    stage_bulk: dict[str, Any] = {}
    for key, entry in wall["stage_bulk"].items():
        updated = dict(entry)
        if (
            key == _STAGE3_AREA_KEY
            and updated.get("cost_status") == _DERIVED_COST_STATUS
            and updated.get("cost_per_kg_usd") is None
        ):
            updated["cost_per_kg_usd"] = in_situ_processing_cost_per_kg(
                "silica", electricity_usd_per_kWh
            )["usd_per_kg"]
        stage_bulk[key] = updated
    rebuilt = dict(wall)
    rebuilt["stage_bulk"] = stage_bulk
    return rebuilt


def _doloma_processing_cost(electricity_usd_per_kWh: float) -> dict[str, Any]:
    temperature_K = _DOLOMA_PROCESS_C + CELSIUS_TO_KELVIN_OFFSET
    flags = [
        _EFFICIENCY_FLAG,
        "cited_example_not_a_kiln_schedule",
        "mass_weighted_janaf_not_measured_doloma_cp",
    ]
    components = []
    total_mass = 0.0
    weighted_energy = 0.0
    for species, table_id in _DOLOMA_COMPONENTS:
        row = _enthalpy_increment(table_id, temperature_K)
        molar_mass = _molar_mass_g_per_mol(species)
        specific = row["enthalpy_kJ_per_mol"] / molar_mass
        components.append({
            "species": species,
            "table_id": table_id,
            "molar_mass_g_per_mol": molar_mass,
            "molar_mass_owner": "simulator.accounting.formulas.resolve_species_formula",
            "specific_energy_MJ_per_kg": specific,
            "enthalpy_kJ_per_mol": row["enthalpy_kJ_per_mol"],
            "enthalpy_status": row["enthalpy_status"],
            "neighbors": row["neighbors"],
        })
        total_mass += molar_mass
        weighted_energy += molar_mass * specific
        if row["fusion_transition"] is None:
            flags.append("heat_of_fusion_not_in_table")
        if row["enthalpy_status"] != "on_printed_grid":
            flags.append(row["enthalpy_status"])
    specific_energy = weighted_energy / total_mass
    flags.append(_sanity_flag(specific_energy))
    return _processing_record(
        material="doloma",
        electricity_usd_per_kWh=electricity_usd_per_kWh,
        specific_energy_MJ_per_kg=specific_energy,
        process_temperature_K=temperature_K,
        process_temperature_citation=(
            "Diaz-Tato et al., Technologies 2025, 13(11), 523: higher "
            "sintering temperatures, such as 1400 C. An example, not a kiln schedule."
        ),
        components=components,
        flags=_unique(flags),
    )


def _silica_processing_cost(electricity_usd_per_kWh: float) -> dict[str, Any]:
    temperature_K = _silica_process_temperature_K()
    species, table_id = _SILICA_COMPONENT
    row = _enthalpy_increment(table_id, temperature_K)
    molar_mass = _molar_mass_g_per_mol(species)
    specific = row["enthalpy_kJ_per_mol"] / molar_mass
    flags = [
        _EFFICIENCY_FLAG,
        "process_temperature_is_catalog_max_operating_not_a_cited_sinter",
        "janaf_quartz_not_fused_silica",
        _sanity_flag(specific),
    ]
    if row["fusion_transition"] is None:
        flags.append("heat_of_fusion_not_in_table")
    if row["enthalpy_status"] != "on_printed_grid":
        flags.append(row["enthalpy_status"])
    return _processing_record(
        material="silica",
        electricity_usd_per_kWh=electricity_usd_per_kWh,
        specific_energy_MJ_per_kg=specific,
        process_temperature_K=temperature_K,
        process_temperature_citation=(
            "fused_silica service_temp.max_operating_C from data/wall_materials.yaml. "
            "Not a cited sinter temperature. JANAF table is O-037 quartz crystal."
        ),
        components=[{
            "species": species,
            "table_id": table_id,
            "molar_mass_g_per_mol": molar_mass,
            "molar_mass_owner": "simulator.accounting.formulas.resolve_species_formula",
            "specific_energy_MJ_per_kg": specific,
            "enthalpy_kJ_per_mol": row["enthalpy_kJ_per_mol"],
            "enthalpy_status": row["enthalpy_status"],
            "neighbors": row["neighbors"],
        }],
        flags=_unique(flags),
    )


def _processing_record(
    *,
    material: str,
    electricity_usd_per_kWh: float,
    specific_energy_MJ_per_kg: float,
    process_temperature_K: float,
    process_temperature_citation: str,
    components: list[dict[str, Any]],
    flags: list[str],
) -> dict[str, Any]:
    from simulator.thermal_budget import KJ_PER_KWH

    usd_per_kg = (
        specific_energy_MJ_per_kg
        * 1000.0
        / KJ_PER_KWH
        / _THERMAL_EFFICIENCY
        * electricity_usd_per_kWh
    )
    return {
        "material": material,
        "usd_per_kg": usd_per_kg,
        "specific_energy_MJ_per_kg": specific_energy_MJ_per_kg,
        "thermal_efficiency": _THERMAL_EFFICIENCY,
        "thermal_efficiency_flag": _EFFICIENCY_FLAG,
        "electricity_usd_per_kWh": electricity_usd_per_kWh,
        "kj_per_kwh": KJ_PER_KWH,
        "kj_per_kwh_owner": "simulator.thermal_budget.KJ_PER_KWH",
        "process_temperature_K": process_temperature_K,
        "process_temperature_citation": process_temperature_citation,
        "components": components,
        "flags": flags,
    }


def _silica_process_temperature_K() -> float:
    from simulator.wall_advisor import load_wall_materials

    service = load_wall_materials()["materials"]["fused_silica"]["service_temp"]
    maximum = service.get("max_operating_C")
    if maximum is None:
        raise ValueError("fused_silica max_operating_C is unset")
    return float(maximum) + CELSIUS_TO_KELVIN_OFFSET


def _enthalpy_increment(table_id: str, temperature_K: float) -> dict[str, Any]:
    from simulator.reference_data.janaf import (
        TABLES_DIR,
        load_table_document,
        table_fusion_transition,
    )

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    table = document["table"]
    if table.get("table_id") != table_id:
        raise ValueError(f"{table_id} table_id mismatch")
    rows = []
    for row in table["values"]:
        enthalpy = row.get("enthalpy_increment")
        temperature = row.get("temperature")
        if not isinstance(enthalpy, Mapping) or not isinstance(temperature, Mapping):
            continue
        if enthalpy.get("value") is None:
            continue
        rows.append((
            float(temperature["value"]),
            float(enthalpy["value"]),
            enthalpy.get("locator"),
        ))
    target = float(temperature_K)
    below = None
    above = None
    exact = None
    for printed_t, value, locator in rows:
        if abs(printed_t - target) <= _TEMPERATURE_MATCH_K:
            exact = (printed_t, value, locator)
        elif printed_t < target and (below is None or printed_t > below[0]):
            below = (printed_t, value, locator)
        elif printed_t > target and (above is None or printed_t < above[0]):
            above = (printed_t, value, locator)
    if exact is not None:
        enthalpy_value = exact[1]
        status = "on_printed_grid"
        neighbors: dict[str, Any] = {"exact": _enthalpy_point(*exact)}
    elif below is None or above is None:
        raise ValueError(f"{table_id} enthalpy bracket missing at {target}")
    else:
        fraction = (target - below[0]) / (above[0] - below[0])
        enthalpy_value = below[1] + fraction * (above[1] - below[1])
        status = "enthalpy_interpolated_between_printed_janaf_rows"
        neighbors = {
            "below": _enthalpy_point(*below),
            "above": _enthalpy_point(*above),
        }
    return {
        "enthalpy_kJ_per_mol": enthalpy_value,
        "enthalpy_status": status,
        "neighbors": neighbors,
        "fusion_transition": table_fusion_transition(table),
    }


def _enthalpy_point(
    temperature_K: float,
    enthalpy_kJ_per_mol: float,
    locator: Any,
) -> dict[str, Any]:
    return {
        "temperature_K": temperature_K,
        "enthalpy_kJ_per_mol": enthalpy_kJ_per_mol,
        "locator": locator,
    }


def _molar_mass_g_per_mol(species: str) -> float:
    from simulator.accounting.formulas import resolve_species_formula

    return float(resolve_species_formula(species).molar_mass_g_per_mol())


def _sanity_flag(specific_energy_MJ_per_kg: float) -> str:
    if 1.0 <= specific_energy_MJ_per_kg <= 2.0:
        return "specific_energy_within_1_to_2_MJ_per_kg_sanity"
    return "specific_energy_outside_1_to_2_MJ_per_kg"


def _unique(items: list[str]) -> list[str]:
    ordered: list[str] = []
    for item in items:
        if item not in ordered:
            ordered.append(item)
    return ordered


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
    from simulator.transport_constants import CARRIER_GAS_PROPERTIES

    bounds = _janaf_cp_bounds(
        _N2_TABLE_ID,
        temperature_K,
        expected_phase="ref",
    )
    if _is_unavailable(bounds):
        return bounds
    bounds["molar_mass_kg_mol"] = CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol
    bounds["molar_mass_owner"] = "simulator.transport_constants.CARRIER_GAS_PROPERTIES"
    return bounds


def _janaf_cp_bounds(
    table_id: str,
    temperature_K: float,
    *,
    expected_phase: str,
) -> dict[str, Any]:
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    table = document["table"]
    if table.get("table_id") != table_id:
        raise ValueError(f"{table_id} table_id mismatch")
    phase = table.get("index_entry", {}).get("state")
    if phase != expected_phase:
        return _unavailable(f"janaf_table_phase_unexpected:{table_id}:{phase}", "J/(mol·K)")
    below = None
    above = None
    exact = None
    for row in table["values"]:
        heat = row.get("heat_capacity")
        temperature = row.get("temperature")
        if not isinstance(heat, Mapping) or not isinstance(temperature, Mapping):
            continue
        if heat.get("value") is None or heat.get("as_published") == "INFINITE":
            continue
        printed_t = float(temperature["value"])
        point = (printed_t, float(heat["value"]), heat.get("locator"))
        if printed_t == temperature_K:
            exact = point
        elif printed_t < temperature_K and (below is None or printed_t > below[0]):
            below = point
        elif printed_t > temperature_K and (above is None or printed_t < above[0]):
            above = point
    payload = {
        "table_id": table_id,
        "phase": phase,
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
