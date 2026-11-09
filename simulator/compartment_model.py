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
from simulator.condensation_routing import designated_stage_number
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
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
) -> tuple[float | None, str, dict[str, Any] | None]:
    if species == "CrO":
        return None, "CrO_has_no_independent_wall_P_sat", None
    if species == "SiO":
        payload = _authoritative_vapour_catalog_payload(vapor_pressure_data)
        if payload is None or pO2_bar is None or pO2_bar <= 0.0:
            return None, "SiO_reaction_wall_pressure_inputs_unavailable", None
        try:
            evaluator = compiled_catalog_for(
                payload,
                emit_u0_request_rules=False,
            ).evaluator_for_hot_train("SiO")
            result = evaluator.evaluate(
                temperature_K,
                source_activity=1.0,
                pO2_bar=pO2_bar,
            )
        except (CatalogCompileError, HotTrainInapplicable, TypeError, ValueError):
            return None, "SiO_reaction_wall_pressure_unavailable", None
        pressure = _number(result.pressure_pa)
        notice = {
            "valid_range_K": tuple(evaluator.valid_temperature_K),
            "out_of_range": bool(result.out_of_range),
            "status": result.status or "in_range",
            "acquisition_flag": result.acquisition_flag,
            "validation_status": getattr(
                result.validation_status, "value", result.validation_status
            ),
            "pressure_observable": getattr(
                evaluator.pressure_observable, "value", evaluator.pressure_observable
            ),
            "authority_level": (
                "extrapolated" if result.out_of_range else "catalog_evaluated"
            ),
        }
        if pressure is None or pressure <= 0.0:
            return None, "SiO_reaction_wall_pressure_not_positive_finite", notice
        return pressure, str(notice["status"]), notice

    extrapolations: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    pressure, refused = _try_antoine_psat_pa(
        species,
        temperature_K,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=extrapolations,
        antoine_extrapolation_warnings=warnings,
    )
    notice = next(iter(extrapolations.values()), None)
    if notice is None:
        notice = {
            "status": "refused" if refused else "in_range",
            "authority_level": "unavailable" if refused else "owner_evaluated",
        }
    if refused or pressure is None or not math.isfinite(float(pressure)) or pressure <= 0.0:
        return None, "elemental_wall_P_sat_unavailable", dict(notice)
    return float(pressure), str(notice.get("status", "computed")), dict(notice)


def _products_per_kg(owner: Any, species: str) -> dict[str, float] | None:
    vapor_pressures = getattr(owner, "vapor_pressures", None)
    product_resolver = getattr(owner, "_condensed_products_kg", None)
    if not isinstance(vapor_pressures, Mapping) or not callable(product_resolver):
        return None
    metals = vapor_pressures.get("metals", {})
    oxides = vapor_pressures.get("oxide_vapors", {})
    row = (metals.get(species) if isinstance(metals, Mapping) else None) or (
        oxides.get(species) if isinstance(oxides, Mapping) else None
    ) or {}
    try:
        products = product_resolver(species, 1.0, row)
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(products, Mapping):
        return None
    clean = {
        str(product): amount
        for product, raw_amount in products.items()
        if (amount := _number(raw_amount)) is not None and amount > 0.0
    }
    return clean or None


def _capture_by_carrier(
    capture_by_stage_product: Mapping[tuple[int, str], float],
    carriers: Mapping[str, float],
    evaporation_owner: Any,
) -> tuple[dict[str, dict[str, float]], dict[str, str], dict[str, dict[str, float]]]:
    products_by_carrier = {
        species: _products_per_kg(evaporation_owner, species)
        for species in carriers
    }
    product_sources: dict[str, set[str]] = {}
    for species, products in products_by_carrier.items():
        for product in products or {species: 1.0}:
            product_sources.setdefault(product, set()).add(species)

    amounts_by_source_stage: dict[tuple[str, str], dict[str, float]] = {}
    unattributed: dict[str, dict[str, float]] = {}
    ambiguous: dict[str, str] = {}
    for (stage_number, product), mass in capture_by_stage_product.items():
        stage = str(stage_number)
        product = str(product)
        sources = product_sources.get(product, set())
        if len(sources) != 1:
            unattributed.setdefault(stage, {})[product] = mass
            if len(sources) > 1:
                for species in sources:
                    ambiguous[species] = "condenser_product_has_multiple_vapor_carriers"
            continue
        species = next(iter(sources))
        products = products_by_carrier[species]
        weight = products.get(product, 1.0) if products is not None else 1.0
        amounts_by_source_stage.setdefault((species, stage), {})[product] = mass / weight

    captured_by_carrier: dict[str, dict[str, float]] = {}
    for (species, stage), product_amounts in amounts_by_source_stage.items():
        products = products_by_carrier[species] or {species: 1.0}
        expected = {key: value for key, value in products.items() if value > 0.0}
        if not set(expected).issubset(product_amounts):
            ambiguous[species] = "condenser_product_split_incomplete"
            continue
        carrier_amounts = [product_amounts[key] for key in expected]
        reference = math.fsum(carrier_amounts) / len(carrier_amounts)
        if any(
            not math.isclose(value, reference, rel_tol=1.0e-9, abs_tol=1.0e-12)
            for value in carrier_amounts
        ):
            ambiguous[species] = "condenser_product_split_does_not_match_owner"
            continue
        captured_by_carrier.setdefault(species, {})[stage] = reference
    if unattributed:
        for species, products in products_by_carrier.items():
            if products is None:
                ambiguous.setdefault(
                    species,
                    "condenser_product_capture_cannot_be_attributed_without_owner",
                )
    return captured_by_carrier, ambiguous, unattributed


def _route_refusal(
    operating_snapshot: Mapping[str, Any],
    route_model: Any,
    species: str,
    segment_name: str,
) -> Mapping[str, Any] | None:
    temperature_refusals = getattr(
        route_model, "wall_temperature_input_refusals", {}
    )
    if isinstance(temperature_refusals, Mapping) and segment_name in temperature_refusals:
        return {"reason": str(temperature_refusals[segment_name])}
    notice = operating_snapshot.get("wall_sticking_alpha_provenance_notice")
    if not isinstance(notice, Mapping):
        notice = getattr(route_model, "last_sticking_alpha_provenance_notice", {})
    if not isinstance(notice, Mapping):
        return None
    for key in (
        "wall_saturation_pressure_refusals_by_species",
        "wall_knudsen_regime_refusals_by_species",
    ):
        by_species = notice.get(key)
        by_segment = by_species.get(species) if isinstance(by_species, Mapping) else None
        refusal = by_segment.get(segment_name) if isinstance(by_segment, Mapping) else None
        if isinstance(refusal, Mapping):
            return refusal
    return None


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
    condensation_model: Any | None = None,
    evaporation_owner: Any | None = None,
) -> dict[str, Any]:
    """Project melt-film, headspace, duct and condenser balances for one hour.

    Candidate wall deposits, local pressures and Kn labels are copied from the
    existing route. Product captures are mapped back through the evaporation
    owner's product projection. No ledger or score input is changed.
    """
    raw_evap = hour_output.get("vapor_species_kg_hr")
    if not isinstance(raw_evap, Mapping):
        raise ValueError("hour output lacks vapor_species_kg_hr mapping")
    evaporation = {str(key): float(value) for key, value in raw_evap.items()}
    if any(not math.isfinite(v) or v < 0.0 for v in evaporation.values()):
        raise ValueError("vapor_species_kg_hr values must be finite and non-negative")

    raw_candidates = operating_snapshot.get("wall_deposition_rate_shadow_candidate")
    candidates = raw_candidates if isinstance(raw_candidates, Mapping) else {}
    capture_by_stage_product: dict[tuple[int, str], float] | None = None
    if condenser_capture_by_stage_species_kg is not None:
        capture_by_stage_product = {}
        for key, raw_capture in condenser_capture_by_stage_species_kg.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError("condenser capture keys must be (stage, species)")
            stage, species = key
            capture = _number(raw_capture)
            if capture is None or capture < 0.0:
                raise ValueError("condenser capture values must be finite and non-negative")
            normalized_key = (int(stage), str(species))
            capture_by_stage_product[normalized_key] = (
                capture_by_stage_product.get(normalized_key, 0.0) + capture
            )
    segments = {str(item.name): item for item in pipe_segments}
    headspace = operating_snapshot.get("species_partial_pressures_mbar")
    headspace = headspace if isinstance(headspace, Mapping) else {}
    pO2_mbar = _number(headspace.get("O2"))
    pO2_bar = pO2_mbar * 1.0e-3 if pO2_mbar is not None else None
    carrier_flow_mol_s = _number(hour_output.get("carrier_flow_mol_s"))
    if carrier_flow_mol_s is not None and carrier_flow_mol_s < 0.0:
        raise ValueError("carrier_flow_mol_s must be non-negative")

    capture_by_carrier: dict[str, dict[str, float]] = {}
    capture_attribution_refusals: dict[str, str] = {}
    unattributed_capture: dict[str, dict[str, float]] = {}
    if capture_by_stage_product is not None:
        capture_by_carrier, capture_attribution_refusals, unattributed_capture = (
            _capture_by_carrier(
                capture_by_stage_product,
                evaporation,
                evaporation_owner,
            )
        )

    duct_nodes: dict[str, dict[str, Any]] = {}
    rows_by_species: dict[str, list[dict[str, Any]]] = {}
    deposits_by_species: dict[str, float] = {}
    unavailable_species: set[str] = set()
    applicable_known: set[str] = set()
    route_partials = getattr(
        condensation_model, "wall_species_partial_pressures_pa_by_segment", {}
    )
    route_partials = route_partials if isinstance(route_partials, Mapping) else {}
    for species in evaporation:
        candidate_segment_names = {
            str(segment_name)
            for segment_name, raw_species in candidates.items()
            if isinstance(raw_species, Mapping) and species in raw_species
        }
        applicable_resolver = getattr(
            condensation_model, "_mixed_temperature_wall_candidate_segments", None
        )
        if callable(applicable_resolver):
            try:
                applicable_segments = list(applicable_resolver(species))
                applicable_known.add(species)
            except (TypeError, ValueError):
                applicable_segments = [segments[name] for name in candidate_segment_names if name in segments]
        else:
            # Candidate names identify supplied rows, not the complete wall
            # route. Keep any rows that can be projected, but withhold the
            # species balance until route applicability is established.
            applicable_segments = [segments[name] for name in candidate_segment_names if name in segments]

        for segment in applicable_segments:
            segment_name = str(getattr(segment, "name", ""))
            candidate_species = candidates.get(segment_name, {})
            record = candidate_species.get(species) if isinstance(candidate_species, Mapping) else None
            record = record if isinstance(record, Mapping) else None
            surface = record.get("surface") if record is not None else None
            surface = surface if isinstance(surface, Mapping) else {}
            transport = record.get("transport") if record is not None else None
            transport = transport if isinstance(transport, Mapping) else {}
            raw_wall_K = surface.get("wall_temperature_K")
            wall_K = _number(raw_wall_K)
            wall_C = _number(getattr(segment, "wall_temperature_C", None))
            if wall_K is None and wall_C is not None:
                wall_K = wall_C + CELSIUS_TO_KELVIN_OFFSET
            if wall_C is None and wall_K is not None:
                wall_C = wall_K - CELSIUS_TO_KELVIN_OFFSET
            try:
                wall_area = _number(segment.surface_area_m2)
                los_area = _wall_geometry_conductance_weight(segment)
            except (AttributeError, TypeError, ValueError):
                wall_area = None
                los_area = None

            route_by_segment = route_partials.get(segment_name, {})
            route_by_segment = route_by_segment if isinstance(route_by_segment, Mapping) else {}
            route_pO2_pa = _number(route_by_segment.get("O2"))
            route_pO2_bar = (
                route_pO2_pa / 100_000.0 if route_pO2_pa is not None else None
            )
            pressure = (
                _number(record.get("species_partial_pressure_pa"))
                if record is not None
                else _number(route_by_segment.get(species))
            )
            regime = str(transport.get("regime_label") or "unavailable")
            refusal = _route_refusal(
                operating_snapshot, condensation_model, species, segment_name
            )
            route_pressure_notice = (
                record.get("wall_saturation_pressure_notice")
                if record is not None
                else None
            )
            if refusal is not None:
                psat, psat_status, pressure_notice = (
                    None,
                    "route_owner_refused_wall_pressure",
                    dict(refusal),
                )
            elif wall_K is None or wall_K <= 0.0:
                psat, psat_status, pressure_notice = (
                    None,
                    "wall_temperature_unavailable",
                    None,
                )
            elif pressure is None:
                psat, psat_status, pressure_notice = (
                    None,
                    "segment_partial_pressure_unavailable",
                    None,
                )
            elif species == "SiO" and route_pO2_bar is None:
                psat, psat_status, pressure_notice = (
                    None,
                    "segment_oxygen_partial_pressure_unavailable",
                    None,
                )
            else:
                psat, psat_status, pressure_notice = _wall_pressure(
                    species, wall_K, route_pO2_bar, vapor_pressure_data
                )
                if isinstance(route_pressure_notice, Mapping):
                    pressure_notice = {
                        **dict(route_pressure_notice),
                        **(pressure_notice or {}),
                    }
            margin = pressure / psat if pressure is not None and psat else None
            deposit = None
            row_status = "unavailable"
            reason = None
            if (
                wall_area is None
                or wall_area <= 0.0
                or los_area is None
                or wall_K is None
            ):
                reason = "segment_geometry_or_wall_temperature_unavailable"
            elif refusal is not None:
                reason = str(refusal.get("reason") or "route_owner_refused_segment")
            elif psat is None:
                reason = psat_status
            elif record is None:
                reason = "applicable_segment_candidate_missing"
            else:
                mol_s = _number(record.get("mol_s"))
                if mol_s is not None and mol_s >= 0.0:
                    try:
                        formula = resolve_species_formula(
                            species, species_formula_registry
                        )
                        deposit = (
                            mol_s
                            * formula.molar_mass_kg_per_mol()
                            * SECONDS_PER_HOUR
                        )
                        row_status = "computed"
                    except (KeyError, TypeError, ValueError):
                        reason = "species_formula_unavailable"
                else:
                    reason = "candidate_mass_unavailable"
            if deposit is None:
                unavailable_species.add(species)
            else:
                deposits_by_species[species] = deposits_by_species.get(species, 0.0) + deposit

            node_row = {
                "status": row_status,
                "unavailable_reason": reason,
                "partial_pressure_pa": pressure,
                "wall_temperature_K": wall_K,
                "wall_saturation_pressure_pa": psat,
                "wall_saturation_status": psat_status,
                "wall_saturation_pressure_notice": pressure_notice,
                "deposition_propensity_margin": margin,
                "deposition_propensity_margin_notice": (
                    None if margin is None else {
                        "status": "computed",
                        "pressure_source": "route_segment_hour_partial_pressure",
                        "pO2_source": (
                            "route_segment_hour_partial_pressure"
                            if species == "SiO"
                            else None
                        ),
                        "wall_saturation_pressure_notice": pressure_notice,
                    }
                ),
                "knudsen_number": _number(transport.get("Kn")),
                "knudsen_regime": regime,
                "transport_path": (
                    "free_molecular_line_of_sight_area_proxy"
                    if regime == "free_molecular"
                    else "existing_series_resistance_route"
                ),
                "line_of_sight_area_proxy_m2": los_area,
                "deposit_candidate_kg_hr": deposit,
            }
            rows_by_species.setdefault(species, []).append({
                "segment": segment_name,
                **node_row,
            })
            node = duct_nodes.setdefault(segment_name, {
                "name": segment_name,
                "wall_temperature_C": wall_C,
                "wall_area_m2": wall_area,
                "species": {},
            })
            node["species"][species] = node_row

    actual_capture_by_stage_species: dict[str, dict[str, float]] | None = None
    designated_capture_by_species: dict[str, dict[str, float]] | None = None
    if capture_by_stage_product is not None:
        actual_capture_by_stage_species = {}
        for (stage, product), mass in capture_by_stage_product.items():
            actual_capture_by_stage_species.setdefault(str(stage), {})[product] = mass
        designated_capture_by_species = {}
        for species, by_stage in capture_by_carrier.items():
            target_stage = designated_stage_number(species)
            designated_capture_by_species[species] = {
                stage: mass
                for stage, mass in by_stage.items()
                if target_stage is not None and int(stage) == target_stage
            }

    balances: dict[str, Any] = {}
    for species, emitted in evaporation.items():
        node_rows = rows_by_species.get(species, ())
        deposited = deposits_by_species.get(species, 0.0)
        capture_unknown = (
            capture_attribution_refusals.get(species)
            if capture_by_stage_product is not None
            else None
        )
        captured_by_stage = (
            capture_by_carrier.get(species, {})
            if capture_by_stage_product is not None and capture_unknown is None
            else None
        )
        captured = (
            math.fsum(captured_by_stage.values())
            if captured_by_stage is not None and capture_unknown is None
            else None
        )
        designated = (
            designated_capture_by_species.get(species, {})
            if designated_capture_by_species is not None
            and capture_unknown is None
            else None
        )
        condenser_fields = {
            "actual_condenser_capture_by_stage_carrier_kg": captured_by_stage,
            "actual_condenser_capture_kg": captured,
            "designated_condenser_capture_by_stage_kg": designated,
            "designated_condenser_capture_kg": (
                math.fsum(designated.values()) if designated is not None else None
            ),
            "actual_condenser_capture_by_stage_species_kg": actual_capture_by_stage_species,
            "unattributed_condenser_capture_by_stage_species_kg": unattributed_capture,
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
            continue
        if (
            species in unavailable_species
            or species not in applicable_known
            or capture_unknown is not None
        ):
            balances[species] = {
                "status": "unavailable",
                "reason": (
                    capture_unknown
                    or "applicable_wall_sink_or_route_applicability_unavailable"
                ),
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": None,
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": None,
            }
            continue
        if carrier_flow_mol_s == 0.0 and captured is None:
            remainder = max(0.0, emitted - deposited)
            accounted = math.fsum((deposited, remainder))
            balances[species] = {
                "status": "ballistic_line_of_sight_only",
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": {
                    row["segment"]: row["deposit_candidate_kg_hr"]
                    for row in node_rows
                },
                **condenser_fields,
                "gas_remaining_without_advective_capture_kg_hr": remainder,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": (accounted - emitted) / emitted,
            }
            continue
        if captured is None:
            balances[species] = {
                "status": "condenser_capture_unavailable",
                "evaporated_kg_hr": emitted,
                "deposited_by_segment_kg_hr": {
                    row["segment"]: row["deposit_candidate_kg_hr"]
                    for row in node_rows
                },
                **condenser_fields,
                "unassigned_after_condenser_kg_hr": None,
                "relative_mass_balance_error": None,
            }
            continue
        if deposited + captured > emitted and not math.isclose(
            deposited + captured, emitted, rel_tol=1.0e-12
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
            continue
        unassigned = max(0.0, emitted - deposited - captured)
        accounted = math.fsum((deposited, captured, unassigned))
        balances[species] = {
            "status": (
                "ballistic_line_of_sight_with_condenser_capture"
                if carrier_flow_mol_s == 0.0
                else "computed"
            ),
            "evaporated_kg_hr": emitted,
            "deposited_by_segment_kg_hr": {
                row["segment"]: row["deposit_candidate_kg_hr"]
                for row in node_rows
            },
            **condenser_fields,
            "condenser_feed_residual_kg_hr": max(0.0, emitted - deposited),
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
                "pO2_bar": pO2_bar,
                "source_flag": _LAG_FLAG,
            },
            "duct_segments": list(duct_nodes.values()),
        },
        "species_balances": balances,
        "reactive_sinks": [
            {"sink": "alkali_on_silica", "status": "not_modelled_in_stage_1"},
            {"sink": "Cr_CrOx_to_Cr2O3", "status": "not_modelled_in_stage_1"},
        ],
        "ledger_effect": "none",
        "score_effect": "none",
    }
