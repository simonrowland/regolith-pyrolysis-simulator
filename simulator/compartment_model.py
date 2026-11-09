"""Read-only compartment report over existing hourly route diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation import (
    _authoritative_vapour_catalog_payload,
    _try_antoine_psat_pa,
)
from simulator.vapour_rail.catalog import (
    CatalogCompileError,
    HotTrainInapplicable,
    compiled_catalog_for,
)
from simulator.wall_deposition import (
    SECONDS_PER_HOUR,
    _wall_geometry_conductance_weight,
)

_LAG_FLAG = "headspace_partials_one_hour_lagged_b1146_period2_artifact"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _wall_pressure(
    species: str,
    temperature_K: float,
    pO2_bar: float | None,
    vapor_pressure_data: Mapping[str, Any] | None,
) -> tuple[float | None, str]:
    if species == "CrO":
        return None, "CrO_has_no_independent_wall_P_sat"
    if species == "SiO":
        payload = _authoritative_vapour_catalog_payload(vapor_pressure_data)
        if payload is None or pO2_bar is None or pO2_bar <= 0.0:
            return None, "SiO_reaction_wall_pressure_inputs_unavailable"
        try:
            evaluator = compiled_catalog_for(
                payload,
                emit_u0_request_rules=False,
            ).evaluator_for_hot_train("SiO")
            # Pure silica is the wall reference: the catalog owns Kp(T)/sqrt(pO2).
            result = evaluator.evaluate(
                temperature_K,
                source_activity=1.0,
                pO2_bar=pO2_bar,
            )
        except (CatalogCompileError, HotTrainInapplicable, TypeError, ValueError):
            return None, "SiO_reaction_wall_pressure_unavailable"
        pressure = _number(result.pressure_pa)
        return (pressure, "computed") if pressure and pressure > 0.0 else (
            None,
            "SiO_reaction_wall_pressure_not_positive_finite",
        )
    pressure, refused = _try_antoine_psat_pa(
        species,
        temperature_K,
        vapor_pressure_data=vapor_pressure_data,
    )
    if refused or pressure is None or not math.isfinite(float(pressure)) or pressure <= 0.0:
        return None, "elemental_wall_P_sat_unavailable"
    return float(pressure), "computed"


def diagnose_compartment_hour(
    hour_output: Mapping[str, Any],
    operating_snapshot: Mapping[str, Any],
    *,
    pipe_segments: Sequence[Any],
    vapor_pressure_data: Mapping[str, Any] | None = None,
    species_formula_registry: Mapping[str, Any] | None = None,
    condenser_capture_by_stage_species_kg: (
        Mapping[tuple[int, str], float] | None
    ) = None,
) -> dict[str, Any]:
    """Project melt-film, headspace, duct and condenser balances for one hour.

    Candidate deposits, segment partial pressures and Kn labels are read from
    the existing condensation route. Stage captures come from the matching
    hourly snapshot delta. Current run output has no carrier-feed ``F``; it
    remains a typed absence and is never defaulted here.
    """
    raw_evap = hour_output.get("vapor_species_kg_hr")
    if not isinstance(raw_evap, Mapping):
        raise ValueError("hour output lacks vapor_species_kg_hr mapping")
    evaporation = {str(key): float(value) for key, value in raw_evap.items()}
    if any(not math.isfinite(v) or v < 0.0 for v in evaporation.values()):
        raise ValueError("vapor_species_kg_hr values must be finite and non-negative")
    candidates = operating_snapshot.get("wall_deposition_rate_shadow_candidate")
    candidates = candidates if isinstance(candidates, Mapping) else {}
    capture_by_species: dict[str, dict[str, float]] = {}
    if condenser_capture_by_stage_species_kg is not None:
        for key, raw_capture in condenser_capture_by_stage_species_kg.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError("condenser capture keys must be (stage, species)")
            stage, species = key
            capture = _number(raw_capture)
            if capture is None or capture < 0.0:
                raise ValueError("condenser capture values must be finite and non-negative")
            stage_captures = capture_by_species.setdefault(str(species), {})
            stage_key = str(stage)
            stage_captures[stage_key] = stage_captures.get(stage_key, 0.0) + capture
    segments = {str(item.name): item for item in pipe_segments}
    pO2_bar = _number(hour_output.get("pO2_bar"))
    carrier_flow_mol_s = _number(hour_output.get("carrier_flow_mol_s"))
    if carrier_flow_mol_s is not None and carrier_flow_mol_s < 0.0:
        raise ValueError("carrier_flow_mol_s must be non-negative")
    headspace = operating_snapshot.get("species_partial_pressures_mbar")
    headspace = headspace if isinstance(headspace, Mapping) else {}

    duct_nodes: list[dict[str, Any]] = []
    rows_by_species: dict[str, list[dict[str, Any]]] = {}
    deposits_by_species: dict[str, float] = {}
    for segment_name, raw_species in candidates.items():
        segment = segments.get(str(segment_name))
        if segment is None or not isinstance(raw_species, Mapping):
            continue
        species_rows: dict[str, Any] = {}
        try:
            los_area_m2 = _wall_geometry_conductance_weight(segment)
        except (AttributeError, TypeError, ValueError):
            los_area_m2 = None
        for species, record in raw_species.items():
            species = str(species)
            if species not in evaporation or not isinstance(record, Mapping):
                continue
            pressure = _number(record.get("species_partial_pressure_pa"))
            surface = record.get("surface")
            surface = surface if isinstance(surface, Mapping) else {}
            wall_K = _number(surface.get("wall_temperature_K"))
            transport = record.get("transport")
            transport = transport if isinstance(transport, Mapping) else {}
            regime = str(transport.get("regime_label") or "unavailable")
            psat, psat_status = (
                (None, "segment_pressure_or_wall_temperature_missing")
                if pressure is None or wall_K is None or wall_K <= 0.0
                else _wall_pressure(species, wall_K, pO2_bar, vapor_pressure_data)
            )
            margin = (
                None
                if psat is None or pressure is None
                else pressure / psat
            )
            mol_s = _number(record.get("mol_s"))
            mass_rate = None
            if psat is not None and mol_s is not None and mol_s >= 0.0:
                try:
                    formula = resolve_species_formula(species, species_formula_registry)
                    mass_rate = (
                        mol_s
                        * formula.molar_mass_kg_per_mol()
                        * SECONDS_PER_HOUR
                    )
                except (KeyError, TypeError, ValueError):
                    pass
            if mass_rate is not None and carrier_flow_mol_s == 0.0:
                if regime != "free_molecular" or los_area_m2 is None:
                    mass_rate = 0.0
                elif los_area_m2 == 0.0:
                    mass_rate = 0.0
            if mass_rate is not None:
                deposits_by_species[species] = deposits_by_species.get(species, 0.0) + mass_rate
            path = (
                "free_molecular_line_of_sight_area_proxy"
                if regime == "free_molecular"
                else "existing_series_resistance_route"
            )
            node_row = {
                "partial_pressure_pa": pressure,
                "wall_saturation_pressure_pa": psat,
                "wall_saturation_status": psat_status,
                "deposition_propensity_margin": margin,
                "knudsen_number": _number(transport.get("Kn")),
                "knudsen_regime": regime,
                "transport_path": path,
                "line_of_sight_area_proxy_m2": los_area_m2,
                "deposit_candidate_kg_hr": mass_rate,
            }
            species_rows[species] = node_row
            rows_by_species.setdefault(species, []).append({
                "segment": str(segment_name),
                **node_row,
            })
        if species_rows:
            duct_nodes.append({
                "name": str(segment_name),
                "wall_temperature_C": float(segment.wall_temperature_C),
                "wall_area_m2": float(segment.surface_area_m2),
                "species": species_rows,
            })

    balances: dict[str, Any] = {}
    for species, emitted in evaporation.items():
        node_rows = rows_by_species.get(species, ())
        deposited = deposits_by_species.get(species, 0.0)
        captures = (
            capture_by_species.get(species, {})
            if condenser_capture_by_stage_species_kg is not None
            else None
        )
        captured = math.fsum(captures.values()) if captures is not None else None
        condenser_fields = {
            "designated_condenser_capture_by_stage_kg": captures,
            "designated_condenser_capture_kg": captured,
        }
        if emitted == 0.0:
            balances[species] = {
                "status": "computed_zero_evaporation",
                "evaporated_kg_hr": 0.0,
                "deposited_by_segment_kg_hr": {},
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": 0.0,
                "relative_mass_balance_error": 0.0,
            }
        elif not node_rows or any(row["deposit_candidate_kg_hr"] is None for row in node_rows):
            balances[species] = {
                "status": "unavailable",
                "reason": "wall_candidate_or_margin_unavailable",
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": None,
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": None,
            }
        elif carrier_flow_mol_s == 0.0:
            if captured is not None and captured > 0.0:
                balances[species] = {
                    "status": "refused",
                    "reason": "condenser_capture_conflicts_with_zero_carrier",
                    "evaporated_kg_hr": emitted,
                    "deposited_by_segment_kg_hr": None,
                    **condenser_fields,
                    "unassigned_after_condenser_kg_hr": None,
                    "relative_mass_balance_error": None,
                }
                continue
            remaining = max(0.0, emitted - deposited)
            balances[species] = {
                "status": "ballistic_line_of_sight_only",
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": {
                    row["segment"]: row["deposit_candidate_kg_hr"] for row in node_rows
                },
                "gas_remaining_without_advective_capture_kg_hr": remaining,
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": (math.fsum((deposited, remaining)) - emitted) / emitted,
            }
        elif captured is not None and deposited + captured > emitted and not math.isclose(
            deposited + captured,
            emitted,
            rel_tol=1.0e-12,
        ):
            balances[species] = {
                "status": "refused",
                "reason": "segment_deposits_and_condenser_capture_exceed_evaporation",
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": None,
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": None,
            }
        else:
            remainder = max(0.0, emitted - deposited - (captured or 0.0))
            if captures is None:
                condenser_feed = max(0.0, emitted - deposited)
                unassigned = None
                accounted = math.fsum((deposited, condenser_feed))
            else:
                condenser_feed = max(0.0, emitted - deposited)
                unassigned = remainder
                accounted = math.fsum((deposited, captured or 0.0, remainder))
            balances[species] = {
                "status": (
                    "computed"
                    if captures is not None
                    else "condenser_capture_unavailable"
                ),
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": {
                    row["segment"]: row["deposit_candidate_kg_hr"] for row in node_rows
                },
                **condenser_fields,
                "condenser_feed_residual_kg_hr": condenser_feed,
                "unassigned_after_condenser_kg_hr": unassigned,
                "relative_mass_balance_error": (accounted - emitted) / emitted,
            }
    return {
        "status": "diagnostic_only",
        "hour": hour_output.get("hour"),
        "campaign": hour_output.get("campaign"),
        "flags": [_LAG_FLAG],
        "carrier_flow": (
            {"status": "typed_absence", "reason": "carrier_feed_not_in_current_hourly_output"}
            if carrier_flow_mol_s is None
            else {"status": "reported_input", "flow_mol_s": carrier_flow_mol_s}
        ),
        "nodes": {
            "melt_film": {"evaporation_kg_hr": evaporation},
            "headspace": {
                "partial_pressures_pa": {
                    str(key): float(value) * 100.0 for key, value in headspace.items()
                    if _number(value) is not None and float(value) >= 0.0
                },
                "source_flag": _LAG_FLAG,
            },
            "duct_segments": duct_nodes,
        },
        "species_balances": balances,
        "reactive_sinks": [
            {"sink": "alkali_on_silica", "status": "not_modelled_in_stage_1"},
            {"sink": "Cr_CrOx_to_Cr2O3", "status": "not_modelled_in_stage_1"},
        ],
        "ledger_effect": "none",
        "score_effect": "none",
    }
