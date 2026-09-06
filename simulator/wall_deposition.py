"""Process-layer wall-deposition transport physics.

Accounting stays phase-blind bookkeeping. These helpers size cold-wall
deposit candidates from series-resistance flux; they do not write the ledger.

Layer rule: process physics does not live in simulator.accounting.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

SECONDS_PER_HOUR = 3600.0


def _record_wall_pressure_notice(
    model: Any,
    kind: str,
    species: str,
    segment: str,
    record: Mapping[str, Any],
) -> None:
    records = model.last_sticking_alpha_provenance_notice.setdefault(kind, {}).setdefault(species, {})
    if any(
        existing == record for key, existing in records.items()
        if key == segment or key.startswith(f"{segment}#")
    ):
        return
    key = segment
    suffix = 2
    while key in records:
        key = f"{segment}#{suffix}"
        suffix += 1
    records[key] = dict(record)


def wall_deposit_candidate_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float | dict[str, Any]:
    return wall_deposit_candidate_for_surface_kg(
        model,
        species=species,
        rate_kg_hr=rate_kg_hr,
        T_cond_C=T_cond_C,
        melt_temperature_C=melt_temperature_C,
        wall_temperature_C=model.wall_temperature_C,
        surface_area_m2=model.wall_surface_area_m2,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )


def wall_deposit_candidates_by_segment_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
    supply_by_segment_kg: Mapping[str, float],
    antoine_extrapolation_warnings: list[str] | None = None,
) -> dict[str, float]:
    from simulator.condensation import (
        WallSaturationPressureRefusal, _deposition_finite_scalar,
    )

    rate_kg_hr = _deposition_finite_scalar("rate_kg_hr", rate_kg_hr)
    if rate_kg_hr <= 0.0 or not model.pipe_segments:
        return {}
    reachable_segments = model._mixed_temperature_wall_candidate_segments(species)
    if not reachable_segments:
        return {}
    candidates: dict[str, float] = {}
    for segment in reachable_segments:
        supply_kg = min(
            max(0.0, _deposition_finite_scalar(
                "segment_supply_kg_hr",
                supply_by_segment_kg.get(segment.name, rate_kg_hr),
            )),
            rate_kg_hr,
        )
        try:
            candidate = wall_deposit_candidate_for_surface_kg(
                model,
                species=species,
                rate_kg_hr=supply_kg,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
                wall_temperature_C=segment.wall_temperature_C,
                surface_area_m2=_wall_geometry_conductance_weight(segment),
                pipe_diameter_m=float(
                    getattr(segment, "inner_diameter_m", model.pipe_diameter_m)
                ),
                regime_factor=_segment_wall_regime_factor(model, segment),
                segment=segment,
                antoine_extrapolation_warnings=antoine_extrapolation_warnings,
            )
        except WallSaturationPressureRefusal as exc:
            record = {
                "status": "refused", "reason": str(exc), "refusal_type": type(exc).__name__,
                "output_status": "status_bearing", "authority_level": "unavailable",
                "wall_temperature_K": (
                    None if getattr(exc, "parameter", None) == "T_wall_K"
                    else segment.wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET
                ),
                "wall_saturation_pressure_pa": None,
            }
            _record_wall_pressure_notice(model, "wall_saturation_pressure_refusals_by_species",
                                         species, segment.name, record)
            continue
        if isinstance(candidate, Mapping) and candidate.get("status") == "unavailable":
            # No wall quantity: leave this supply on the existing gas-train route.
            continue
        if candidate > 0.0:
            candidates[segment.name] = min(candidate, supply_kg)
    total = sum(candidates.values())
    if total > rate_kg_hr > 0.0:
        scale = rate_kg_hr / total
        candidates = {
            name: value * scale
            for name, value in candidates.items()
        }
    by_segment = getattr(
        model,
        "last_wall_deposition_rate_shadow_candidate",
        None,
    )
    if isinstance(by_segment, dict):
        molar_mass_kg_mol = resolve_species_formula(
            species,
            getattr(model, "species_formula_registry", None),
        ).molar_mass_kg_per_mol()
        for segment_name, candidate_kg_h in candidates.items():
            record = by_segment.get(segment_name, {}).get(species)
            if not isinstance(record, dict):
                continue
            reported_mol_s = (
                candidate_kg_h / molar_mass_kg_mol / SECONDS_PER_HOUR
            )
            transport_capacity_mol_s = float(
                record["uncapped_transport_capacity_mol_s"]
            )
            record["mol_s"] = reported_mol_s
            record["supply_limited"] = (
                transport_capacity_mol_s > reported_mol_s
            )
            record["transport_limited"] = (
                transport_capacity_mol_s <= reported_mol_s
            )
            record["uncertainty"]["p50_mol_s"] = reported_mol_s
    return candidates


def wall_deposit_candidate_for_surface_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
    wall_temperature_C: float,
    surface_area_m2: float,
    pipe_diameter_m: float | None = None,
    regime_factor: float | None = None,
    segment: Any | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float | dict[str, Any]:
    from simulator.condensation import (
        DepositionInputRefusal,
        WallSaturationPressureRefusal,
        _alpha_s_spec_from_entry,
        _deposition_finite_scalar,
        _flowing_species_partial_pressures_pa,
        _knudsen_number,
        _liner_material_config,
        _required_record_alpha_s,
        _series_resistance_deposition_flux_mol_m2_s,
        _species_vapor_data,
        _transport_parameter_notice,
        _wall_alpha_record,
        _wall_material_config,
        classify_knudsen_regime,
    )

    temperature_refusal = getattr(model, "wall_temperature_input_refusals", {}).get(
        str(getattr(segment, "name", "default_pipe"))
    )
    if temperature_refusal:
        refusal = DepositionInputRefusal("T_wall_K", None, temperature_refusal)
        record = {
            "status": "refused", "reason": str(refusal), "refusal_type": type(refusal).__name__,
            "output_status": "status_bearing", "authority_level": "unavailable",
            "wall_temperature_K": None, "wall_saturation_pressure_pa": None,
        }
        _record_wall_pressure_notice(
            model, "wall_saturation_pressure_refusals_by_species",
            species, str(segment.name), record,
        )
        return {**record, "status": "unavailable", "species": species}

    rate_kg_hr = _deposition_finite_scalar("rate_kg_hr", rate_kg_hr)
    surface_area_m2 = _deposition_finite_scalar("surface_area_m2", surface_area_m2)
    if rate_kg_hr <= 0.0 or surface_area_m2 <= 0.0:
        return 0.0

    materials = getattr(model, "materials", None)
    wall_config = _wall_material_config(materials)
    segment_liner = str(getattr(segment, "liner_material", "") or "")
    wall_liner = str(wall_config.get("liner_material") or "")
    # Inspect the selected declaration before the frozen scalar reader clips it.
    for config in (
        _liner_material_config(segment_liner, materials) if segment_liner else {},
        wall_config,
        _liner_material_config(wall_liner, materials) if wall_liner else {},
    ):
        entries = config.get("alpha_s_by_species") or {}
        if not isinstance(entries, Mapping) or entries.get(species) is None:
            continue
        raw_alpha = _alpha_s_spec_from_entry(species, entries[species])
        if raw_alpha is not None and not isinstance(raw_alpha, Mapping):
            declared_alpha = _deposition_finite_scalar("alpha_s", raw_alpha)
            if not 0.0 <= declared_alpha <= 1.0:
                raise DepositionInputRefusal(
                    "alpha_s", raw_alpha, "sticking coefficient must be within [0, 1]"
                )
        break
    T_wall_K = max(float(wall_temperature_C) + CELSIUS_TO_KELVIN_OFFSET, 1.0)
    alpha_record = _wall_alpha_record(
        species,
        materials,
        segment=segment,
        T_K=T_wall_K,
    )
    alpha_s = _required_record_alpha_s(
        alpha_record, species=species, site="wall_deposit_candidate_for_surface_kg"
    )

    segment_name = str(getattr(segment, "name", "")) if segment is not None else ""
    overhead_pressure_pa = None
    P_local_pa = None
    if alpha_s != 0.0:
        vapor_pressure_data = getattr(model, "vapor_pressure_data", None)
        overhead_pressure_pa = float(model.overhead_pressure_mbar) * 100.0
        partial_pressures_pa = getattr(
            model,
            "wall_species_partial_pressures_pa",
            {},
        )
        pressure_source_label = "wall_species_partial_pressures_pa"
        if (
            not partial_pressures_pa
            and bool(getattr(model, "_species_partial_pressures_configured", False))
        ):
            pressure_source_label = "species_partial_pressures_mbar"
            partial_pressures_pa = _flowing_species_partial_pressures_pa(
                {},
                overhead_pressure_pa,
                reported_partial_pressures_mbar=getattr(
                    model,
                    "species_partial_pressures_mbar",
                    {},
                ),
            )
        segment_partial_pressures = getattr(
            model,
            "wall_species_partial_pressures_pa_by_segment",
            {},
        ).get(segment_name, {})
        pressure_source = (
            segment_partial_pressures
            if segment_name and segment_partial_pressures
            else partial_pressures_pa
        )
        if segment_name and segment_partial_pressures:
            pressure_source_label = f"wall_species_partial_pressures_pa_by_segment.{segment_name}"
        if species not in pressure_source:
            raise DepositionInputRefusal(
                "P_local_pa", None, f"wall species partial pressure is unconfigured for {species}"
            )
        P_local_pa = _deposition_finite_scalar("P_local_pa", pressure_source[species])
        if P_local_pa is not None and P_local_pa < 0.0:
            raise DepositionInputRefusal("P_local_pa", P_local_pa, "must be non-negative")

    rate_diagnostic: dict[str, Any] = {"status": "computed"}
    if alpha_s == 0.0:
        # Only a declared/evaluated coefficient can prove a non-sticking zero.
        flux = 0.0
        rate_diagnostic["zero_reason"] = "declared_zero_sticking_coefficient"
        rate_diagnostic["zero_source"] = alpha_record["source"]
    elif P_local_pa == 0.0:
        # The species key exists in a declared/computed pressure map; absence refuses above.
        flux = 0.0
        rate_diagnostic["zero_reason"] = "computed_zero_species_partial_pressure"
        rate_diagnostic["zero_source"] = pressure_source_label
    else:
        try:
            gas_temperature_C = float(model.gas_temperature_C)
        except (TypeError, ValueError) as exc:
            raise DepositionInputRefusal(
                "T_gas_K",
                getattr(model, "gas_temperature_C", None),
                "must be numeric",
            ) from exc
        if not math.isfinite(gas_temperature_C):
            raise DepositionInputRefusal(
                "T_gas_K",
                model.gas_temperature_C,
                "must be finite",
            )
        T_gas_K = gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET
        if T_gas_K <= 0.0:
            raise DepositionInputRefusal(
                "T_gas_K",
                model.gas_temperature_C,
                "must be above absolute zero",
            )
        applied_pipe_diameter_m = (
            float(pipe_diameter_m)
            if pipe_diameter_m is not None
            else model.pipe_diameter_m
        )
        applied_regime_factor = (
            float(regime_factor)
            if regime_factor is not None
            else model.regime_factor
        )
        applied_regime_factor = _deposition_finite_scalar(
            "regime_factor", applied_regime_factor
        )
        flux = _series_resistance_deposition_flux_mol_m2_s(
            species, P_local_pa, T_wall_K, alpha_s,
            pipe_diameter_m=applied_pipe_diameter_m,
            stir_factor=model.stir_factor,
            radial_stir_factor=model.radial_stir_factor,
            regime_factor=applied_regime_factor,
            T_gas_K=T_gas_K,
            overhead_pressure_pa=overhead_pressure_pa,
            carrier_gas=str(getattr(model, "carrier_gas", "N2") or "N2"),
            vapor_pressure_data=vapor_pressure_data,
            # Same eligibility gate as the stage band-sampling site in
            # condensation.py: the SiO disproportionation backstop may
            # materialize product only at/below the declared condensation
            # temperature. Without this the helper's True default let a HOT
            # wall (above T_cond) mass-deposit reactive products — the inverse
            # of the 07fa3fe stage fix (wall-path residual tracked as t-404).
            reactive_product_backstop=(
                species == 'SiO'
                and float(wall_temperature_C) <= float(T_cond_C)
            ),
            antoine_extrapolation_warnings=antoine_extrapolation_warnings,
            diagnostic_out=rate_diagnostic,
        )
    if rate_diagnostic.get("wall_saturation_pressure_refused"):
        refusal_reason = rate_diagnostic["wall_saturation_pressure_refusal_reason"]
        _record_wall_pressure_notice(model, "wall_saturation_pressure_refusals_by_species",
            species, str(getattr(segment, "name", "default_pipe")), {
            "status": "refused",
            "reason": refusal_reason,
            "output_status": "status_bearing",
            "wall_temperature_K": T_wall_K,
            "wall_saturation_pressure_pa": None,
            **rate_diagnostic.get("wall_saturation_pressure_notice", {}),
        })
        source_data = _species_vapor_data(species, vapor_pressure_data=vapor_pressure_data)
        source_only_wall_channel = (
            source_data.get("fit_target") == "standard_reaction_term"
            and "pure_component_antoine" not in source_data
        )
        if source_only_wall_channel:
            from engines.builtin.vapor_pressure import _coefficient_mapping

            coefficient_block = "antoine"
            if coefficient_block in source_data:
                # A range label can also mask an incomplete or invalid fit.
                # Validate the declared fit before treating it as domain-only.
                coefficients = _coefficient_mapping(
                    source_data, coefficient_block, temperature_K=T_wall_K
                )
                coefficients = {
                    key: _deposition_finite_scalar(
                        f"{coefficient_block}.{key}", coefficients.get(key)
                    )
                    for key in ("A", "B", "C")
                }
                # Reaction-term log fits may have A <= 0; their denominator must still be valid.
                if T_wall_K + coefficients["C"] <= 0.0:
                    raise DepositionInputRefusal(
                        coefficient_block, coefficients,
                        "Antoine fit requires T_wall_K + C > 0 and wall fits require A > 0",
                    )
            # Valid physics outside the wall model's domain marks and skips.
            # A melt standard-reaction term is not a pure-species wall P_sat;
            # without a wall sidecar it cannot supply a wall quantity either.
            return {
                "status": "unavailable",
                "reason": refusal_reason,
                "terminal_refusal": False,
                "species": species,
                "wall_temperature_K": T_wall_K,
            }
        return {
            "status": "unavailable", "reason": refusal_reason,
            "terminal_refusal": False, "species": species,
            "wall_temperature_K": T_wall_K,
        }
    pressure_notice = rate_diagnostic.get("wall_saturation_pressure_notice")
    if pressure_notice is not None:
        _record_wall_pressure_notice(model, "wall_saturation_pressure_extrapolations_by_species",
            species, str(getattr(segment, "name", "default_pipe")), pressure_notice)
    rate_diagnostic["species_partial_pressure_pa"] = P_local_pa
    rate_diagnostic["total_pressure_pa"] = overhead_pressure_pa
    wall_saturation_pressure_pa = rate_diagnostic.get(
        "wall_saturation_pressure_pa"
    )
    rate_diagnostic["supersaturated"] = (
        P_local_pa > wall_saturation_pressure_pa
        if wall_saturation_pressure_pa is not None and P_local_pa is not None
        else None
    )

    budget_kg_hr = _wall_deposition_flux_budget_kg_hr(
        species=species,
        flux_mol_m2_s=flux,
        surface_area_m2=surface_area_m2,
        formula_registry=getattr(model, "species_formula_registry", None),
    )
    carrier_gas = str(getattr(model, "carrier_gas", "N2") or "N2")
    knudsen_number = None if "zero_reason" in rate_diagnostic else _knudsen_number(
        overhead_pressure_pa,
        T_gas_K,
        applied_pipe_diameter_m,
        carrier_gas=carrier_gas,
    )
    parameter_status = _wall_rate_parameter_status(alpha_record)
    segment_name = str(getattr(segment, "name", "default_pipe"))
    by_segment = getattr(
        model,
        "last_wall_deposition_rate_shadow_candidate",
        None,
    )
    if isinstance(by_segment, dict):
        reported_kg_h = min(rate_kg_hr, budget_kg_hr)
        molar_mass_kg_mol = resolve_species_formula(
            species,
            getattr(model, "species_formula_registry", None),
        ).molar_mass_kg_per_mol()
        reported_mol_s = (
            reported_kg_h / molar_mass_kg_mol / SECONDS_PER_HOUR
        )
        available_supply_mol_s = (
            rate_kg_hr / molar_mass_kg_mol / SECONDS_PER_HOUR
        )
        transport_capacity_mol_s = (
            budget_kg_hr / molar_mass_kg_mol / SECONDS_PER_HOUR
        )
        by_segment.setdefault(segment_name, {})[species] = {
            **rate_diagnostic,
            "mol_s": reported_mol_s,
            "available_supply_mol_s": available_supply_mol_s,
            "uncapped_transport_capacity_mol_s": transport_capacity_mol_s,
            "supply_limited": budget_kg_hr > rate_kg_hr,
            "transport_limited": budget_kg_hr <= rate_kg_hr,
            "inventory_effect": False,
            "transport": {} if knudsen_number is None else {
                "Kn": knudsen_number,
                "regime_label": classify_knudsen_regime(knudsen_number).value,
                "correction_model": "series_resistance_continuity_v1",
                "correction_parameter_status": "bounded_prior",
                "notice": _transport_parameter_notice(species, carrier_gas),
            },
            "surface": {
                "wall_temperature_K": T_wall_K,
                "material": str(getattr(segment, "liner_material", "")),
                "alpha_c": alpha_s,
                "alpha_c_status": parameter_status,
                "alpha_c_source": str(alpha_record.get("source") or ""),
                "reevap_model": "bulk_hkl_equilibrium_pressure",
            },
            "parameter_status": parameter_status,
            "uncertainty": {
                "p05_mol_s": None,
                "p50_mol_s": reported_mol_s,
                "p95_mol_s": None,
                "basis": "unqualified_until_same_surface_calibration",
            },
            "authoritative_for_lifespan": False,
        }
    return min(rate_kg_hr, budget_kg_hr)


def _wall_rate_parameter_status(alpha_record: Mapping[str, Any]) -> str:
    """Map generic source provenance onto the coating interface vocabulary."""

    raw = str(
        alpha_record.get("qualification_status")
        or alpha_record.get("status")
        or ""
    ).strip().lower()
    allowed = {
        "measured_same_surface",
        "measured_proxy_surface",
        "bounded_prior",
        "uncalibrated",
    }
    if raw in allowed:
        return raw
    source_class = str(alpha_record.get("source_class") or "").lower()
    if "uncert" in raw or not alpha_record.get("source"):
        return "uncalibrated"
    if "proxy" in raw or "proxy" in source_class:
        return "measured_proxy_surface"
    return "bounded_prior"


def _wall_deposition_flux_budget_kg_hr(
    *,
    species: str,
    flux_mol_m2_s: float,
    surface_area_m2: float,
    formula_registry: Mapping[str, Any] | None = None,
) -> float:
    values = {
        "flux_mol_m2_s": flux_mol_m2_s,
        "surface_area_m2": surface_area_m2,
    }
    for name, value in values.items():
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise AccountingError(f"wall capture {name} must be finite and non-negative")

    # Premise: the transport solver returns deposited molar flux at the wall.
    # Algebra: m_dot = J*A*M. Unit check:
    # (mol/m2/s)*(m2)*(kg/mol)*(3600 s/h) = kg/h.
    # Sanity: the budget is zero at zero flux/area and linear before supply cap.
    molar_mass_kg_mol = resolve_species_formula(
        species,
        formula_registry,
    ).molar_mass_kg_per_mol()
    return (
        flux_mol_m2_s
        * surface_area_m2
        * molar_mass_kg_mol
        * SECONDS_PER_HOUR
    )


def _segment_wall_regime_factor(model: Any, segment: Any) -> float:
    from simulator.condensation import _knudsen_number, _knudsen_regime_factor

    try:
        pressure_pa = float(model.overhead_pressure_mbar) * 100.0
        gas_temperature_K = max(
            float(model.gas_temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
            1.0,
        )
        diameter_m = float(getattr(segment, "inner_diameter_m", model.pipe_diameter_m))
        carrier_gas = str(getattr(model, "carrier_gas", "N2") or "N2")
        kn = _knudsen_number(
            pressure_pa,
            gas_temperature_K,
            diameter_m,
            carrier_gas=carrier_gas,
        )
        return _knudsen_regime_factor(kn)
    except Exception:
        return float(getattr(model, "regime_factor", 1.0) or 1.0)


def _wall_geometry_conductance_weight(segment: Any) -> float:
    """Named assumption: free-molecular view-factor/LOS area proxy.

    TODO(P0a): replace this proxy with the pinned aperture/tube conductance
    ladder once carrier/regime plumbing is available for lab surfaces.
    """

    try:
        raw_area = segment.surface_area_m2
    except AttributeError as exc:
        raise AccountingError(
            "wall geometry surface_area_m2 is missing; "
            "missing area is not proof of zero deposition weight"
        ) from exc
    except ValueError as exc:
        raise AccountingError(
            f"wall geometry surface_area_m2 is invalid: {exc}; "
            "invalid area is not proof of zero deposition weight"
        ) from exc
    try:
        area_m2 = float(raw_area)
    except (TypeError, ValueError) as exc:
        raise AccountingError(
            f"wall geometry surface_area_m2 is not numeric: {raw_area!r}; "
            "unknown area is not proof of zero deposition weight"
        ) from exc
    if not math.isfinite(area_m2) or area_m2 <= 0.0:
        raise AccountingError(
            f"wall geometry surface_area_m2 is not a positive finite area: "
            f"{raw_area!r}; invalid area is not proof of zero deposition weight"
        )
    view_factor = getattr(segment, "view_factor_from_melt", None)
    line_of_sight = getattr(segment, "line_of_sight_to_melt", None)
    if view_factor is None and line_of_sight is None:
        return area_m2
    if line_of_sight is False:
        return 0.0
    if view_factor is None:
        view_factor_value = 1.0
    else:
        try:
            view_factor_value = float(view_factor)
        except (TypeError, ValueError) as exc:
            raise AccountingError(
                f"wall geometry view_factor_from_melt is not numeric: "
                f"{view_factor!r}; unknown view factor is not proof of zero "
                f"deposition weight"
            ) from exc
        if not math.isfinite(view_factor_value):
            raise AccountingError(
                f"wall geometry view_factor_from_melt is non-finite: "
                f"{view_factor!r}; unknown view factor is not proof of zero "
                f"deposition weight"
            )
        if not 0.0 <= view_factor_value <= 1.0:
            raise AccountingError(
                f"wall geometry view_factor_from_melt is outside [0, 1]: "
                f"{view_factor!r}; invalid view factor is not proof of zero "
                f"deposition weight"
            )
    return area_m2 * view_factor_value
